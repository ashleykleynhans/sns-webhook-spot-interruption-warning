import json
from unittest.mock import patch, MagicMock
import pytest
import webhook
from webhook import create_app


class TestCreateApp:
    def test_create_app_with_config(self, test_config):
        app = create_app(test_config)
        assert app is not None

    def test_create_app_without_config_loads_and_validates(self, test_config):
        with patch('webhook.load_config', return_value=test_config):
            with patch('webhook.validate_config') as mock_validate:
                app = create_app()
                assert app is not None
                mock_validate.assert_called_once()


class TestHealthCheck:
    def test_get_health(self, client):
        response = client.get('/')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'ok'


class TestErrorHandlers:
    def test_404(self, client):
        response = client.get('/nonexistent')
        assert response.status_code == 404
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert 'not found' in data['msg']

    def test_500(self, test_config):
        app = create_app(test_config)

        @app.route('/trigger-500')
        def trigger_500():
            raise Exception('boom')

        app.config['TESTING'] = False
        app.config['PROPAGATE_EXCEPTIONS'] = False
        client = app.test_client()
        response = client.get('/trigger-500')
        assert response.status_code == 500
        data = json.loads(response.data)
        assert data['status'] == 'error'
        assert data['msg'] == 'Internal Server Error'


class TestWebhookHandlerSpotInterruption:
    @patch('webhook.requests.post')
    @patch('webhook.get_spot_request_for_instance_id')
    @patch('webhook.get_ec2_instance')
    @patch('webhook.drain_instance_from_elb_target_groups')
    @patch('webhook.run_jenkins_job')
    def test_spot_interruption_full_flow(
        self,
        mock_jenkins,
        mock_drain,
        mock_get_instance,
        mock_get_spot_req,
        mock_post,
        client,
        spot_interruption_payload,
    ):
        mock_instance = MagicMock()
        mock_instance.private_ip_address = '10.0.0.1'
        mock_get_instance.return_value = mock_instance
        mock_get_spot_req.return_value = {
            'Status': {'Code': 'marked-for-termination', 'Message': 'Instance marked'},
        }
        mock_slack_response = MagicMock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_slack_response

        response = client.post(
            '/',
            data=json.dumps(spot_interruption_payload),
            content_type='application/json',
        )

        assert response.status_code == 200
        mock_drain.assert_called_once_with('us-east-1', 'i-0123456789abcdef0')
        mock_jenkins.assert_called_once()
        mock_post.assert_called_once()


class TestWebhookHandlerRebalance:
    @patch('webhook.requests.post')
    @patch('webhook.get_ec2_instance')
    @patch('webhook.drain_instance_from_elb_target_groups')
    @patch('webhook.run_jenkins_job')
    def test_rebalance_does_not_drain(
        self,
        mock_jenkins,
        mock_drain,
        mock_get_instance,
        mock_post,
        client,
        rebalance_recommendation_payload,
    ):
        mock_instance = MagicMock()
        mock_get_instance.return_value = mock_instance
        mock_slack_response = MagicMock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_slack_response

        response = client.post(
            '/',
            data=json.dumps(rebalance_recommendation_payload),
            content_type='application/json',
        )

        assert response.status_code == 200
        mock_drain.assert_not_called()
        mock_jenkins.assert_called_once()


class TestWebhookHandlerNotificationWithoutJenkins:
    @patch('webhook.requests.post')
    @patch('webhook.get_ec2_instance')
    @patch('webhook.run_jenkins_job')
    def test_notification_without_jenkins_block_skips_jenkins(
        self, mock_jenkins, mock_get_instance, mock_post, test_config_no_jenkins,
    ):
        app = create_app(test_config_no_jenkins)
        app.config['TESTING'] = True
        client = app.test_client()

        mock_get_instance.return_value = MagicMock()
        mock_slack_response = MagicMock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_slack_response

        payload = {
            'Type': 'Notification',
            'Message': json.dumps({
                'detail-type': 'EC2 Spot Instance Interruption Warning',
                'region': 'us-east-1',
                'detail': {'instance-id': 'i-abc'},
            }),
        }
        client.post('/', data=json.dumps(payload), content_type='application/json')
        mock_jenkins.assert_not_called()


class TestWebhookHandlerJenkinsRegionScoping:
    @patch('webhook.requests.post')
    @patch('webhook.get_ec2_instance')
    @patch('webhook.drain_instance_from_elb_target_groups')
    @patch('webhook.run_jenkins_job')
    def test_jenkins_skipped_when_region_not_in_job_regions(
        self,
        mock_jenkins,
        mock_drain,
        mock_get_instance,
        mock_post,
        client,
    ):
        # us-east-2 region: spot interruption job is only us-east-1
        payload = {
            'Type': 'Notification',
            'Message': json.dumps({
                'detail-type': 'EC2 Spot Instance Interruption Warning',
                'region': 'us-east-2',
                'detail': {'instance-id': 'i-abc'},
            }),
        }
        mock_get_instance.return_value = MagicMock()
        mock_slack_response = MagicMock()
        mock_slack_response.status_code = 200
        mock_slack_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_slack_response

        client.post('/', data=json.dumps(payload), content_type='application/json')

        mock_jenkins.assert_not_called()
