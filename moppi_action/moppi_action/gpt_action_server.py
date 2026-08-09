import time
import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

# 우리가 방금 만든 통신 규칙(Action) 불러오기!
from moppi_interfaces.action import Conversation

class GptActionServer(Node):
    def __init__(self):
        super().__init__('gpt_action_server')
        # 'start_conversation'이라는 이름의 창구를 엽니다.
        self._action_server = ActionServer(
            self,
            Conversation,
            'start_conversation',
            self.execute_callback)
        self.get_logger().info('🤖 가짜 GPT 대화 노드가 준비되었습니다!')

    def execute_callback(self, goal_handle):
        # 1. C++ 뇌로부터 질문(Goal) 수신
        user_text = goal_handle.request.user_input
        self.get_logger().info(f'📥 수신된 질문: "{user_text}"')
        
        # 2. 스트리밍 답변 흉내내기 (Feedback)
        fake_words = ["안", "녕", "?", " ", "난", " ", "모", "피", "야", "!"]
        feedback_msg = Conversation.Feedback()
        feedback_msg.current_status = "GPT 스트리밍 중..."
        
        for word in fake_words:
            feedback_msg.partial_word = word
            self.get_logger().info(f'스트리밍(TTS 전송용): {word}')
            goal_handle.publish_feedback(feedback_msg)
            time.sleep(0.3) # 0.3초마다 한 글자씩 뱉음 (API 지연시간 흉내)
            
        # 3. 최종 결과(Result) 반환
        goal_handle.succeed()
        result = Conversation.Result()
        result.success = True
        result.final_response = "안녕? 난 모피야!"
        
        self.get_logger().info('✅ 대화 처리 완료!')
        return result

def main(args=None):
    rclpy.init(args=args)
    node = GptActionServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
