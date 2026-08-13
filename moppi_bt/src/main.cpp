#include <ament_index_cpp/get_package_share_directory.hpp>
#include <rclcpp/rclcpp.hpp>
#include <behaviortree_ros2/bt_action_node.hpp>
#include <moppi_interfaces/action/conversation.hpp>

using moppi_interfaces::action::Conversation;

class AskGptNode : public BT::RosActionNode<Conversation>
{
public:
    AskGptNode(const std::string& name,
               const BT::NodeConfig& conf,
               const BT::RosNodeParams& params)
        : BT::RosActionNode<Conversation>(name, conf, params)
    {}

    static BT::PortsList providedPorts()
    {
        return providedBasicPorts({
            BT::InputPort<std::string>("user_text", "사용자가 할 말")
        });
    }

    bool setGoal(Conversation::Goal& goal) override
    {
        std::string user_input;
        getInput("user_text", user_input);
        goal.user_input = user_input;
        
        // 💡 해결 1: node_.lock()을 사용해 안전하게 로거(Logger) 불러오기
        auto rclcpp_node = node_.lock();
        RCLCPP_INFO(rclcpp_node->get_logger(), "🧠 뇌 -> 입으로 명령 전송: %s", user_input.c_str());
        return true;
    }

    // 💡 해결 2: 리턴 타입을 void에서 BT::NodeStatus로 변경
    BT::NodeStatus onFeedback(const std::shared_ptr<const Conversation::Feedback> feedback) override
    {
        auto rclcpp_node = node_.lock();
        RCLCPP_INFO(rclcpp_node->get_logger(), "👂 뇌에서 스트리밍 수신: %s", feedback->partial_word.c_str());
        
        // 💡 해결 3: "아직 피드백 받는 중(RUNNING)이야!" 라고 뇌에 보고하기
        return BT::NodeStatus::RUNNING; 
    }

    BT::NodeStatus onResultReceived(const rclcpp_action::ClientGoalHandle<Conversation>::WrappedResult& wr) override
    {
        auto rclcpp_node = node_.lock();
        RCLCPP_INFO(rclcpp_node->get_logger(), "✅ 뇌 최종 답변 수신 완료: %s", wr.result->final_response.c_str());
        return BT::NodeStatus::SUCCESS;
    }

    BT::NodeStatus onFailure(BT::ActionNodeErrorCode error) override
    {
        auto rclcpp_node = node_.lock();
        RCLCPP_ERROR(rclcpp_node->get_logger(), "❌ 통신 실패! 에러 코드: %d", error);
        return BT::NodeStatus::FAILURE;
    }
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    auto nh = std::make_shared<rclcpp::Node>("moppi_brain");

    BT::BehaviorTreeFactory factory;
    BT::RosNodeParams params;
    params.nh = nh;
    params.default_port_value = "/start_conversation";

    

   // (이전 AskGpt 등록 부분은 그대로 둠)
    factory.registerNodeType<AskGptNode>("AskGpt", params);

// 💡 패키지 경로를 찾아 XML 파일을 로드하도록 변경
    std::string pkg_path = ament_index_cpp::get_package_share_directory("moppi_bt");
    std::string xml_file = pkg_path + "/behavior_trees/main_tree.xml";

// FromText가 아니라 FromFile을 사용합니다.
    auto tree = factory.createTreeFromFile(xml_file);
    RCLCPP_INFO(nh->get_logger(), "🧠 Moppi의 외부 XML 뇌가 활성화되었습니다!");

    tree.tickWhileRunning(); 

    rclcpp::shutdown();
    return 0;
}
