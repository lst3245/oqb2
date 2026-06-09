"""Unit tests for LLM client payload building and response parsing."""
import json
import unittest
from types import SimpleNamespace

from app.llm_client import (
    _build_chat_payload,
    _build_responses_payload,
    _extract_message_parts,
    _extract_responses_parts,
    _messages_to_responses_input,
    _resolve_reasoning,
    parse_request_extra_json,
)


def _cfg(**kwargs):
    defaults = dict(
        model_name='test-model',
        max_output_tokens=4096,
        temperature=0.0,
        service_tier='',
        service_tier_batch='',
        api_protocol='chat',
        reasoning_effort='',
        reasoning_summary='',
        reasoning_max_tokens=None,
        request_extra_json=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestParseRequestExtraJson(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_request_extra_json(''), {})

    def test_valid_object(self):
        self.assertEqual(parse_request_extra_json('{"a": 1}'), {'a': 1})

    def test_rejects_array(self):
        with self.assertRaises(ValueError):
            parse_request_extra_json('[]')


class TestMessagesToResponsesInput(unittest.TestCase):
    def test_system_becomes_instructions(self):
        instructions, items = _messages_to_responses_input([
            {'role': 'system', 'content': 'Be helpful.'},
            {'role': 'user', 'content': 'Hi'},
        ])
        self.assertEqual(instructions, 'Be helpful.')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], {'role': 'user', 'content': 'Hi'})

    def test_assistant_uses_plain_string_not_input_text(self):
        _, items = _messages_to_responses_input([
            {'role': 'user', 'content': 'Hi'},
            {'role': 'assistant', 'content': 'Hello there'},
        ])
        self.assertEqual(items[1], {'role': 'assistant', 'content': 'Hello there'})
        self.assertNotIn('input_text', str(items))

    def test_multimodal_user_uses_message_wrapper(self):
        _, items = _messages_to_responses_input([{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': 'look at this'},
                {'type': 'image_url',
                 'image_url': {'url': 'data:image/jpeg;base64,abc'}},
            ],
        }])
        self.assertEqual(items[0]['type'], 'message')
        self.assertEqual(items[0]['role'], 'user')
        self.assertEqual(items[0]['content'][0]['type'], 'input_text')
        self.assertEqual(items[0]['content'][1]['type'], 'input_image')


class TestPayloadBuilding(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.app = create_app()
        self.app.config['LLM_REASONING_EFFORT_DEFAULT'] = 'off'
        self.app.config['LLM_REASONING_SUMMARY_DEFAULT'] = 'auto'
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_chat_payload_unchanged_when_reasoning_off(self):
        payload = _build_chat_payload(_cfg(), [
            {'role': 'user', 'content': 'ping'},
        ])
        self.assertEqual(payload['model'], 'test-model')
        self.assertIn('messages', payload)
        self.assertNotIn('reasoning', payload)

    def test_chat_payload_includes_reasoning(self):
        payload = _build_chat_payload(_cfg(reasoning_effort='high'), [
            {'role': 'user', 'content': 'ping'},
        ])
        self.assertEqual(payload['reasoning']['effort'], 'high')

    def test_responses_payload_shape(self):
        payload = _build_responses_payload(_cfg(
            api_protocol='responses',
            reasoning_effort='medium',
            reasoning_summary='auto',
        ), [
            {'role': 'system', 'content': 'Solve carefully.'},
            {'role': 'user', 'content': '2+2?'},
        ])
        self.assertEqual(payload['instructions'], 'Solve carefully.')
        self.assertEqual(payload['input'], '2+2?')
        self.assertEqual(payload['reasoning']['effort'], 'medium')
        self.assertEqual(payload['reasoning']['summary'], 'auto')

    def test_claude_responses_uses_adaptive_thinking(self):
        payload = _build_responses_payload(_cfg(
            model_name='Claude-Opus-4.7',
            reasoning_effort='high',
            reasoning_summary='auto',
        ), [{'role': 'user', 'content': 'ping'}])
        self.assertNotIn('reasoning', payload)
        self.assertEqual(payload['thinking'],
                         {'type': 'adaptive', 'display': 'summarized'})
        self.assertEqual(payload['output_config'], {'effort': 'high'})

    def test_claude_reasoning_summary_none_omits_display(self):
        payload = _build_responses_payload(_cfg(
            model_name='Claude-Sonnet-4.6',
            reasoning_effort='medium',
            reasoning_summary='none',
        ), [{'role': 'user', 'content': 'ping'}])
        self.assertEqual(payload['thinking'], {'type': 'adaptive'})
        self.assertEqual(payload['output_config'], {'effort': 'medium'})

    def test_responses_multi_turn_stays_list(self):
        payload = _build_responses_payload(_cfg(), [
            {'role': 'user', 'content': 'Hi'},
            {'role': 'assistant', 'content': 'Hello'},
            {'role': 'user', 'content': 'Again?'},
        ])
        self.assertIsInstance(payload['input'], list)
        self.assertEqual(len(payload['input']), 3)

    def test_merge_request_extra(self):
        payload = _build_chat_payload(_cfg(
            request_extra_json='{"top_p": 0.9}',
        ), [{'role': 'user', 'content': 'x'}])
        self.assertEqual(payload['top_p'], 0.9)

    def test_resolve_reasoning_inherits_system_default(self):
        self.app.config['LLM_REASONING_EFFORT_DEFAULT'] = 'low'
        out = _resolve_reasoning(_cfg())
        self.assertEqual(out['effort'], 'low')


class TestResponseParsing(unittest.TestCase):
    def test_extract_message_parts_with_reasoning(self):
        data = {
            'choices': [{
                'finish_reason': 'stop',
                'message': {
                    'content': 'Answer: A',
                    'reasoning_content': 'Worked it out step by step.',
                },
            }],
        }
        text, reasoning, finish = _extract_message_parts(data)
        self.assertEqual(text, 'Answer: A')
        self.assertIn('step by step', reasoning)
        self.assertEqual(finish, 'stop')

    def test_extract_responses_parts(self):
        data = {
            'output_text': 'Final',
            'output': [
                {'type': 'reasoning', 'content': 'Thinking...'},
                {'type': 'message', 'content': [
                    {'type': 'output_text', 'text': 'Also here'},
                ]},
            ],
            'status': 'completed',
        }
        text, reasoning, finish = _extract_responses_parts(data)
        self.assertTrue(text)
        self.assertIn('Thinking', reasoning)
        self.assertEqual(finish, 'completed')


if __name__ == '__main__':
    unittest.main()
