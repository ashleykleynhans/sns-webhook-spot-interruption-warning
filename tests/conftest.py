import json
import pytest
import webhook


@pytest.fixture
def test_config():
    return {
        'slack': {
            'token': 'xoxb-test-token',
            'channels': {
                'us-east-1': 'aws-alerts-prod',
                'us-east-2': 'aws-alerts-test',
            },
        },
        'jenkins': {
            'url': 'https://jenkins.example.com',
            'username': 'test-user',
            'password': 'test-pass',
        },
        'environments': {
            'us-east-1': 'prod',
            'us-east-2': 'test',
        },
        'notification_types': [
            {
                'name': 'ec2-spot-interruption',
                'detail_type': 'EC2 Spot Instance Interruption Warning',
                'drain_target_groups': True,
                'jenkins': {
                    'jobs': [
                        {
                            'endpoint_url': 'remove-host-from-checkmk-prod/buildWithParameters?SERVER_IP={{ SERVER_IP }}',
                            'regions': ['us-east-1'],
                        }
                    ]
                },
            },
            {
                'name': 'ec2-rebalance-recommendation',
                'detail_type': 'EC2 Instance Rebalance Recommendation',
                'drain_target_groups': False,
                'jenkins': {
                    'jobs': [
                        {
                            'endpoint_url': 'replace-ec2-instance-{{ ENVIRONMENT }}/buildWithParameters?INSTANCE_ID={{ INSTANCE_ID }}',
                            'regions': ['us-east-1', 'us-east-2'],
                        }
                    ]
                },
            },
        ],
    }


@pytest.fixture
def test_config_no_jenkins():
    return {
        'slack': {
            'token': 'xoxb-test-token',
            'channels': {
                'us-east-1': 'aws-alerts-prod',
            },
        },
        'notification_types': [
            {
                'name': 'ec2-spot-interruption',
                'detail_type': 'EC2 Spot Instance Interruption Warning',
                'drain_target_groups': False,
            },
        ],
    }


@pytest.fixture
def app(test_config):
    app = webhook.create_app(test_config)
    app.config['TESTING'] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def spot_interruption_payload():
    return {
        'Type': 'Notification',
        'MessageId': 'test-message-id',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789012:spot-interruption',
        'Message': json.dumps({
            'detail-type': 'EC2 Spot Instance Interruption Warning',
            'region': 'us-east-1',
            'detail': {
                'instance-id': 'i-0123456789abcdef0',
                'instance-action': 'terminate',
            },
        }),
        'Timestamp': '2026-06-11T08:00:00.000Z',
    }


@pytest.fixture
def rebalance_recommendation_payload():
    return {
        'Type': 'Notification',
        'MessageId': 'test-message-id',
        'TopicArn': 'arn:aws:sns:us-east-1:123456789012:rebalance',
        'Message': json.dumps({
            'detail-type': 'EC2 Instance Rebalance Recommendation',
            'region': 'us-east-1',
            'detail': {
                'instance-id': 'i-0123456789abcdef0',
            },
        }),
        'Timestamp': '2026-06-11T08:00:00.000Z',
    }
