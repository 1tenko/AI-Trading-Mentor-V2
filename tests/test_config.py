import pytest

from mentor.config import ConfigError, load_config


def test_load_config_requires_an_api_key(tmp_path):
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        load_config({}, tmp_path / ".env")


def test_load_config_reads_an_api_key_from_the_environment(tmp_path):
    config = load_config({"OPENAI_API_KEY": "test-key"}, tmp_path / ".env")

    assert config.api_key == "test-key"
    assert config.model == "gpt-5.6-sol"
