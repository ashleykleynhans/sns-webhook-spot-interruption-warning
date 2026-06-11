import sys
from unittest.mock import patch, mock_open, MagicMock
import pytest
import webhook


class TestGetArgs:
    def test_default_args(self):
        with patch('sys.argv', ['webhook.py']):
            args = webhook.get_args()
            assert args.port == 8090
            assert args.host == '0.0.0.0'

    def test_custom_args(self):
        with patch('sys.argv', ['webhook.py', '-p', '9000', '-H', '127.0.0.1']):
            args = webhook.get_args()
            assert args.port == 9000
            assert args.host == '127.0.0.1'

    def test_long_form_args(self):
        with patch('sys.argv', ['webhook.py', '--port', '7000', '--host', '1.2.3.4']):
            args = webhook.get_args()
            assert args.port == 7000
            assert args.host == '1.2.3.4'


class TestLoadConfig:
    def test_load_config_success(self):
        config_yaml = "slack:\n  token: xoxb-loaded\n"
        with patch('builtins.open', mock_open(read_data=config_yaml)):
            config = webhook.load_config()
            assert config == {'slack': {'token': 'xoxb-loaded'}}

    def test_load_config_file_not_found(self):
        with patch('builtins.open', side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError):
                webhook.load_config()


class TestValidateConfig:
    def test_none_config(self):
        with pytest.raises(ValueError, match='empty'):
            webhook.validate_config(None)

    def test_missing_slack(self):
        with pytest.raises(ValueError, match="'slack' section"):
            webhook.validate_config({})

    def test_missing_slack_token(self):
        config = {'slack': {'channels': {}}, 'notification_types': []}
        with pytest.raises(ValueError, match="'token' not found"):
            webhook.validate_config(config)

    def test_missing_slack_channels(self):
        config = {'slack': {'token': 'x'}, 'notification_types': []}
        with pytest.raises(ValueError, match="'channels' not found"):
            webhook.validate_config(config)

    def test_jenkins_missing_url(self):
        config = {
            'slack': {'token': 'x', 'channels': {}},
            'jenkins': {'username': 'u', 'password': 'p'},
            'notification_types': [],
        }
        with pytest.raises(ValueError, match="'url' not found in 'jenkins'"):
            webhook.validate_config(config)

    def test_jenkins_missing_username(self):
        config = {
            'slack': {'token': 'x', 'channels': {}},
            'jenkins': {'url': 'https://j', 'password': 'p'},
            'notification_types': [],
        }
        with pytest.raises(ValueError, match="'username' not found in 'jenkins'"):
            webhook.validate_config(config)

    def test_jenkins_missing_password(self):
        config = {
            'slack': {'token': 'x', 'channels': {}},
            'jenkins': {'url': 'https://j', 'username': 'u'},
            'notification_types': [],
        }
        with pytest.raises(ValueError, match="'password' not found in 'jenkins'"):
            webhook.validate_config(config)

    def test_missing_notification_types(self):
        config = {'slack': {'token': 'x', 'channels': {}}}
        with pytest.raises(ValueError, match="'notification_types' not found"):
            webhook.validate_config(config)

    def test_valid_config_returns_silently(self, test_config):
        # Should not raise
        assert webhook.validate_config(test_config) is None

    def test_valid_config_without_jenkins(self, test_config_no_jenkins):
        assert webhook.validate_config(test_config_no_jenkins) is None


class TestGetSlackUrl:
    def test_default_url(self, test_config_no_jenkins):
        url = webhook.get_slack_url(test_config_no_jenkins)
        assert url == 'https://slack.com/api/chat.postMessage'

    def test_custom_url(self):
        config = {
            'slack': {
                'token': 'xoxb-tok',
                'url': 'https://hooks.example.com/services',
                'channels': {},
            },
        }
        url = webhook.get_slack_url(config)
        assert url == 'https://hooks.example.com/services/xoxb-tok'


class TestMain:
    def test_main_starts_app(self, test_config):
        with patch('sys.argv', ['webhook.py']):
            with patch.object(webhook, 'app') as mock_app:
                mock_app.run = MagicMock()
                webhook.main()
                mock_app.run.assert_called_once_with(host='0.0.0.0', port=8090)

    def test_main_exits_when_app_is_none(self):
        with patch('sys.argv', ['webhook.py']):
            with patch.object(webhook, 'app', None):
                with pytest.raises(SystemExit):
                    webhook.main()
