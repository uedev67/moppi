import os
import json
import base64
import asyncio
import threading
import queue
import sounddevice as sd
import numpy as np
import websockets

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from moppi_interfaces.action import Conversation

# 성공하신 최신 모델명으로 연결됩니다.
MODEL = os.getenv("OPENAI_MODEL", "gpt-realtime-2.1-mini")
WS_URL = f"wss://api.openai.com/v1/realtime?model={MODEL}"

DEVICE_RATE = 48000  
CHUNK = 1024

class RealtimeConversationServer(Node):
    def __init__(self):
        super().__init__('realtime_conversation_server')
        self._action_server = ActionServer(
            self, Conversation, '/start_conversation',
            execute_callback=self.execute_callback,
            goal_callback=self.goal_callback,
            cancel_callback=self.cancel_callback
        )
        self.get_logger().info("🚀 젯슨 실시간 대화 서버 (스피커 독립 스레드 모드) 준비 완료!")
        
        # 💡 [핵심] asyncio 큐 대신, 스피커 전용의 안정적인 큐 사용
        self.audio_out_queue = queue.Queue()
        self.stop_event = threading.Event()

    def goal_callback(self, goal_request):
        return GoalResponse.ACCEPT

    def cancel_callback(self, cancel_request):
        return CancelResponse.ACCEPT

    def execute_callback(self, goal_handle):
        self.get_logger().info("📥 액션 골(Goal) 수신됨! 대화 세션 시작 시도...")
        
        # 💡 스피커 전용 독립 스레드 가동 (ALSA 드라이버 충돌 방지)
        self.stop_event.clear()
        self.speaker_thread = threading.Thread(target=self.speaker_worker, daemon=True)
        self.speaker_thread.start()

        future = asyncio.run_coroutine_threadsafe(
            self.async_execute(goal_handle), self.loop
        )
        return future.result()

    def speaker_worker(self):
        """ALSA 오디오 드라이버와 충돌하지 않도록 완벽히 격리된 스피커 전용 스레드"""
        try:
            with sd.OutputStream(samplerate=DEVICE_RATE, channels=2, dtype='int16') as stream:
                self.get_logger().info("🔊 스피커 출력 스트림 대기 중...")
                while not self.stop_event.is_set():
                    try:
                        data = self.audio_out_queue.get(timeout=0.1)
                        stream.write(data)
                    except queue.Empty:
                        continue
        except Exception as e:
            self.get_logger().error(f"❌ 스피커 하드웨어 에러: {e}")

    async def async_execute(self, goal_handle):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self.get_logger().error("OPENAI_API_KEY가 없습니다!")
            goal_handle.abort()
            return Conversation.Result(success=False, disconnect_reason="No API Key")

        headers = {
            "Authorization": f"Bearer {api_key}"
        }
        feedback_msg = Conversation.Feedback()
        result = Conversation.Result()

        try:
            self.mic_stream = sd.InputStream(
                samplerate=DEVICE_RATE, channels=1, dtype='int16', blocksize=CHUNK
            )
            self.mic_stream.start()
            self.get_logger().info("🎙️ 마이크 48kHz 스트림 오픈 성공")
        except Exception as e:
            self.get_logger().error(f"❌ 마이크 장치 열기 실패: {e}")
            goal_handle.abort()
            return Conversation.Result(success=False, disconnect_reason="Audio Device Error")

        try:
            async with websockets.connect(WS_URL, extra_headers=headers) as websocket:
                self.websocket = websocket
                self.get_logger().info("✅ OpenAI 실시간 서버 접속 성공! 대화 시작.")
                
                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["audio", "text"],
                        "instructions": "너는 다정하고 친절한 AI 반려 로봇 '모삐'야. 짧고 자연스럽게 대화해줘.",
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 400
                        },
                        "voice": "alloy",
                        "input_audio_transcription": {
                            "model": "whisper-1"
                        }
                    }
                }
                await self.websocket.send(json.dumps(session_update))

                feedback_msg.dialogue_state = "LISTENING"
                feedback_msg.emotion = "NEUTRAL"
                goal_handle.publish_feedback(feedback_msg)

                receive_task = asyncio.create_task(self.safe_receive(websocket, goal_handle, feedback_msg))
                mic_task = asyncio.create_task(self.safe_mic_send(goal_handle))

                while not goal_handle.is_cancel_requested:
                    await asyncio.sleep(0.1)

                receive_task.cancel()
                mic_task.cancel()
        except Exception as e:
            self.get_logger().error(f"❌ 웹소켓 통신 중 에러 발생: {e}")

        self.websocket = None
        self.mic_stream.stop()
        self.mic_stream.close()
        
        # 종료 시 스피커 스레드 정리
        self.stop_event.set()
        if hasattr(self, 'speaker_thread'):
            self.speaker_thread.join(timeout=1.0)
            
        goal_handle.canceled()
        result.success = True
        result.disconnect_reason = "정상 종료"
        return result

    async def safe_mic_send(self, goal_handle):
        try:
            silence_counter = 100  
            while not goal_handle.is_cancel_requested:
                result = await asyncio.to_thread(self.mic_stream.read, CHUNK)
                if result is not None:
                    data, status = result
                    if data is not None and len(data) > 0:
                        flat_data = data.flatten()
                        vol = float(np.abs(flat_data).max() * 100) if len(flat_data) > 0 else 0.0
                        
                        if vol >= 400000:
                            silence_counter = 0  
                        else:
                            silence_counter += 1

                        if silence_counter > 20:
                            flat_data = np.zeros_like(flat_data)

                        downsampled_24k = flat_data[::2]
                        base64_audio = base64.b64encode(downsampled_24k.tobytes()).decode("utf-8")
                        msg = {
                            "type": "input_audio_buffer.append",
                            "audio": base64_audio
                        }
                        if hasattr(self, 'websocket') and self.websocket:
                            await self.websocket.send(json.dumps(msg))
                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.get_logger().error(f"❌ safe_mic_send 치명적 에러: {e}")

    async def safe_receive(self, websocket, goal_handle, feedback_msg):
        try:
            async for message in websocket:
                event = json.loads(message)
                event_type = event.get("type")

                if event_type == "error":
                    self.get_logger().error(f"🚨 [OpenAI 에러]: {event.get('error')}")

                elif event_type == "input_audio_buffer.speech_started":
                    self.get_logger().info("👂 [OpenAI VAD] 사용자의 말을 듣기 시작했습니다!")
                elif event_type == "input_audio_buffer.speech_stopped":
                    self.get_logger().info("🛑 [OpenAI VAD] 사용자의 말이 끝난 것을 감지했습니다!")

                elif event_type == "conversation.item.input_audio.transcription.completed":
                    transcript = event.get("transcript", "")
                    self.get_logger().info(f"📝 [OpenAI가 인식한 내 목소리]: {transcript}")

                elif event_type in ["response.audio_transcript.delta", "response.output_audio_transcript.delta"]:
                    delta = event.get("delta", "")
                    print(delta, end="", flush=True)
                elif event_type in ["response.audio_transcript.done", "response.output_audio_transcript.done"]:
                    print("\n")

                # 💡 버려지던 오디오 데이터를 큐에 넣음
                elif event_type in ["response.audio.delta", "response.output_audio.delta"]:
                    feedback_msg.dialogue_state = "SPEAKING"
                    goal_handle.publish_feedback(feedback_msg)
                    
                    audio_bytes = base64.b64decode(event["delta"])
                    audio_24k = np.frombuffer(audio_bytes, dtype=np.int16)
                    upsampled_48k = np.repeat(audio_24k, 2)
                    
                    # 💡 Stereo로 변환 후 안전한 큐에 적재!
                    stereo_data = np.column_stack((upsampled_48k, upsampled_48k))
                    self.audio_out_queue.put(stereo_data)

                elif event_type in ["response.audio.done", "response.output_audio.done"]:
                    feedback_msg.dialogue_state = "LISTENING"
                    goal_handle.publish_feedback(feedback_msg)

                # 로그 스팸 방지
                elif event_type not in ["input_audio_buffer.append", "rate_limits.updated", "response.content_part.done", "conversation.item.done", "response.output_item.done", "response.done", "session.updated", "conversation.item.created", "response.created", "response.output_item.added", "response.content_part.added", "input_audio_buffer.committed"]:
                    self.get_logger().info(f"🔎 [기타 이벤트]: {event_type}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.get_logger().error(f"❌ safe_receive 치명적 에러: {e}")

def main(args=None):
    if not rclpy.ok():
        rclpy.init(args=args)
        
    node = RealtimeConversationServer()
    
    loop = asyncio.new_event_loop()
    def start_background_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=start_background_loop, args=(loop,), daemon=True)
    t.start()
    node.loop = loop

    executor = rclpy.executors.MultiThreadedExecutor()
    try:
        rclpy.spin(node, executor=executor)
    except KeyboardInterrupt:
        pass
    finally:
        loop.call_soon_threadsafe(loop.stop)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()