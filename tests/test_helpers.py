import json
from unittest.mock import patch, MagicMock
import pytest
import webhook


class TestEc2Helpers:
    @patch('webhook.boto3.client')
    def test_get_ec2_client(self, mock_boto):
        webhook.get_ec2_client('us-east-1')
        mock_boto.assert_called_once_with('ec2', region_name='us-east-1')

    @patch('webhook.boto3.client')
    def test_get_elb_client(self, mock_boto):
        webhook.get_elb_client('us-west-2')
        mock_boto.assert_called_once_with('elbv2', region_name='us-west-2')

    @patch('webhook.boto3.resource')
    def test_get_ec2_resource(self, mock_boto):
        webhook.get_ec2_resource('us-east-2')
        mock_boto.assert_called_once_with('ec2', region_name='us-east-2')

    @patch('webhook.get_ec2_resource')
    def test_get_ec2_instance_returns_instance(self, mock_resource):
        mock_ec2 = MagicMock()
        mock_resource.return_value = mock_ec2
        result = webhook.get_ec2_instance('us-east-1', 'i-abc')
        mock_ec2.Instance.assert_called_once_with('i-abc')

    @patch('webhook.get_ec2_resource', side_effect=Exception('boom'))
    def test_get_ec2_instance_returns_none_on_error(self, mock_resource):
        result = webhook.get_ec2_instance('us-east-1', 'i-abc')
        assert result is None


class TestTargetGroupHelpers:
    @patch('webhook.get_elb_client')
    def test_get_target_groups(self, mock_get_elb):
        mock_elb = MagicMock()
        mock_elb.describe_target_groups.return_value = {
            'TargetGroups': [{'TargetGroupArn': 'arn:tg1'}, {'TargetGroupArn': 'arn:tg2'}],
        }
        mock_get_elb.return_value = mock_elb
        result = webhook.get_target_groups('us-east-1')
        assert len(result) == 2

    @patch('webhook.get_elb_client')
    def test_get_elb_target_health(self, mock_get_elb):
        mock_elb = MagicMock()
        mock_elb.describe_target_health.return_value = {
            'TargetHealthDescriptions': [
                {'Target': {'Id': 'i-1'}, 'TargetHealth': {'State': 'healthy'}},
                {'Target': {'Id': 'i-2'}, 'TargetHealth': {'State': 'draining'}},
            ],
        }
        mock_get_elb.return_value = mock_elb
        result = webhook.get_elb_target_health('us-east-1', 'arn:tg1')
        assert result == {'i-1': 'healthy', 'i-2': 'draining'}


class TestDrain:
    @patch('webhook.get_elb_target_health')
    @patch('webhook.get_target_groups')
    @patch('webhook.get_elb_client')
    def test_drain_deregisters_when_instance_is_member(self, mock_get_elb, mock_tgs, mock_health):
        mock_tgs.return_value = [
            {'TargetGroupArn': 'arn:tg1', 'TargetGroupName': 'tg1'},
        ]
        mock_health.return_value = {'i-abc': 'healthy'}
        mock_elb = MagicMock()
        mock_get_elb.return_value = mock_elb

        webhook.drain_instance_from_elb_target_groups('us-east-1', 'i-abc')

        mock_elb.deregister_targets.assert_called_once_with(
            TargetGroupArn='arn:tg1',
            Targets=[{'Id': 'i-abc'}],
        )

    @patch('webhook.get_elb_target_health')
    @patch('webhook.get_target_groups')
    @patch('webhook.get_elb_client')
    def test_drain_skips_when_instance_not_in_target_group(self, mock_get_elb, mock_tgs, mock_health):
        mock_tgs.return_value = [
            {'TargetGroupArn': 'arn:tg1', 'TargetGroupName': 'tg1'},
        ]
        mock_health.return_value = {'i-other': 'healthy'}
        mock_elb = MagicMock()
        mock_get_elb.return_value = mock_elb

        webhook.drain_instance_from_elb_target_groups('us-east-1', 'i-abc')

        mock_elb.deregister_targets.assert_not_called()


class TestSpotRequest:
    @patch('webhook.get_ec2_client')
    def test_get_spot_request_found(self, mock_get_ec2):
        mock_ec2 = MagicMock()
        mock_ec2.describe_spot_instance_requests.return_value = {
            'SpotInstanceRequests': [
                {'InstanceId': 'i-other', 'Status': {'Code': 'fulfilled'}},
                {'InstanceId': 'i-abc', 'Status': {'Code': 'marked-for-termination'}},
            ],
        }
        mock_get_ec2.return_value = mock_ec2
        result = webhook.get_spot_request_for_instance_id('us-east-1', 'i-abc')
        assert result['Status']['Code'] == 'marked-for-termination'

    @patch('webhook.get_ec2_client')
    def test_get_spot_request_not_found(self, mock_get_ec2):
        mock_ec2 = MagicMock()
        mock_ec2.describe_spot_instance_requests.return_value = {'SpotInstanceRequests': []}
        mock_get_ec2.return_value = mock_ec2
        result = webhook.get_spot_request_for_instance_id('us-east-1', 'i-abc')
        assert result is None


class TestJenkinsCrumb:
    @patch('webhook.requests.get')
    def test_get_jenkins_crumb(self, mock_get, test_config):
        mock_response = MagicMock()
        mock_response.json.return_value = {'crumb': 'abc123'}
        mock_get.return_value = mock_response
        result = webhook.get_jenkins_crumb(test_config)
        assert result == 'abc123'


class TestRunJenkinsJob:
    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_substitutes_server_ip(self, mock_post, mock_crumb, test_config):
        mock_post.return_value = MagicMock(status_code=201)
        mock_instance = MagicMock()
        mock_instance.private_ip_address = '10.0.0.5'

        webhook.run_jenkins_job(
            test_config,
            'us-east-1',
            'job-name/buildWithParameters?SERVER_IP={{ SERVER_IP }}',
            'i-abc',
            mock_instance,
        )

        called_url = mock_post.call_args[0][0]
        assert 'SERVER_IP=10.0.0.5' in called_url

    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_substitutes_instance_id(self, mock_post, mock_crumb, test_config):
        mock_post.return_value = MagicMock(status_code=201)
        mock_instance = MagicMock()

        webhook.run_jenkins_job(
            test_config,
            'us-east-1',
            'job/buildWithParameters?INSTANCE_ID={{ INSTANCE_ID }}',
            'i-abc',
            mock_instance,
        )

        called_url = mock_post.call_args[0][0]
        assert 'INSTANCE_ID=i-abc' in called_url

    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_substitutes_environment(self, mock_post, mock_crumb, test_config):
        mock_post.return_value = MagicMock(status_code=201)
        mock_instance = MagicMock()

        webhook.run_jenkins_job(
            test_config,
            'us-east-1',
            'replace-ec2-instance-{{ ENVIRONMENT }}/buildWithParameters',
            'i-abc',
            mock_instance,
        )

        called_url = mock_post.call_args[0][0]
        assert 'replace-ec2-instance-prod' in called_url

    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_no_call_when_instance_is_none(self, mock_post, mock_crumb, test_config):
        webhook.run_jenkins_job(
            test_config,
            'us-east-1',
            'any',
            'i-abc',
            None,
        )
        mock_post.assert_not_called()

    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_returns_quietly_when_environment_unknown(self, mock_post, mock_crumb):
        config = {
            'jenkins': {'url': 'https://j', 'username': 'u', 'password': 'p'},
            'environments': {'us-east-1': 'prod'},
        }
        mock_instance = MagicMock()
        webhook.run_jenkins_job(
            config,
            'eu-west-1',
            'replace-ec2-instance-{{ ENVIRONMENT }}/buildWithParameters',
            'i-abc',
            mock_instance,
        )
        mock_post.assert_not_called()

    @patch('webhook.get_jenkins_crumb', return_value='crumb')
    @patch('webhook.requests.post')
    def test_swallows_exception_on_non_201(self, mock_post, mock_crumb, test_config, capsys):
        mock_post.return_value = MagicMock(status_code=500)
        mock_instance = MagicMock()

        webhook.run_jenkins_job(
            test_config,
            'us-east-1',
            'job/buildWithParameters?INSTANCE_ID={{ INSTANCE_ID }}',
            'i-abc',
            mock_instance,
        )

        captured = capsys.readouterr()
        assert 'Unable to invoke Jenkins job' in captured.out


class TestSendSlackNotification:
    @patch('webhook.requests.post')
    def test_sends_message_to_correct_channel(self, mock_post, test_config, app):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        sns_message = {
            'detail-type': 'EC2 Instance Rebalance Recommendation',
            'region': 'us-east-1',
            'detail': {'instance-id': 'i-abc'},
        }

        with app.test_request_context():
            webhook.send_slack_notification(test_config, sns_message)

        body = mock_post.call_args.kwargs['json']
        assert body['channel'] == '#aws-alerts-prod'
        assert body['attachments'][0]['title'] == 'EC2 Instance Rebalance Recommendation'

    @patch('webhook.get_spot_request_for_instance_id')
    @patch('webhook.requests.post')
    def test_appends_spot_reason_when_available(
        self, mock_post, mock_get_spot_req, test_config, app,
    ):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response
        mock_get_spot_req.return_value = {
            'Status': {'Code': 'mark-for-termination', 'Message': 'because'},
        }

        sns_message = {
            'detail-type': 'EC2 Spot Instance Interruption Warning',
            'region': 'us-east-1',
            'detail': {'instance-id': 'i-abc'},
        }

        with app.test_request_context():
            webhook.send_slack_notification(test_config, sns_message)

        body = mock_post.call_args.kwargs['json']
        text = body['attachments'][0]['text']
        assert 'reason_code' in text
        assert 'because' in text

    @patch('webhook.requests.post')
    def test_returns_500_when_slack_fails(self, mock_post, test_config, app):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {'error': 'slack down'}
        mock_post.return_value = mock_response

        sns_message = {
            'detail-type': 'EC2 Instance Rebalance Recommendation',
            'region': 'us-east-1',
            'detail': {'instance-id': 'i-abc'},
        }

        with app.test_request_context():
            response = webhook.send_slack_notification(test_config, sns_message)
            assert response.status_code == 500
