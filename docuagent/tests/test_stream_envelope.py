"""NDJSON stream envelope (schema_version / event_id / seq).

ROADMAP §4.4: every streamed event carries a protocol version, a unique event
id and a per-stream sequence number, so a client can detect dropped frames,
resume from the last seen seq, and refuse events whose shape it does not
understand. Envelope keys are additive — the event body is unchanged, so older
consumers keep working.
"""

import unittest

import main


class EnvelopEventTest(unittest.TestCase):
    def test_adds_schema_event_id_and_seq(self) -> None:
        event = {"type": "reasoning", "text": "思考"}
        enveloped = main.envelop_event(event, 3)
        self.assertEqual(enveloped["schema_version"], main.NDJSON_SCHEMA_VERSION)
        self.assertEqual(enveloped["seq"], 3)
        self.assertEqual(len(enveloped["event_id"]), 32)  # uuid4 hex
        self.assertEqual(enveloped["type"], "reasoning")
        self.assertEqual(enveloped["text"], "思考")

    def test_event_body_is_unchanged_in_shape(self) -> None:
        event = {"type": "done", "result": {"status": "ok"}}
        enveloped = main.envelop_event(event, 1)
        self.assertEqual(enveloped["result"], {"status": "ok"})
        # Every original key survives verbatim.
        for key in event:
            self.assertIn(key, enveloped)

    def test_seq_is_sequential_across_a_stream(self) -> None:
        events = [
            {"type": "reasoning", "text": "a"},
            {"type": "content", "text": "b"},
            {"type": "done", "result": {}},
        ]
        seqs = [
            main.envelop_event(event, index)["seq"]
            for index, event in enumerate(events, start=1)
        ]
        self.assertEqual(seqs, [1, 2, 3])

    def test_event_ids_are_unique(self) -> None:
        ids = {
            main.envelop_event({"type": "x"}, 1)["event_id"]
            for _ in range(50)
        }
        self.assertEqual(len(ids), 50)

    def test_error_event_gets_envelope_too(self) -> None:
        event = {"type": "error", "error": "流内失败"}
        enveloped = main.envelop_event(event, 7)
        self.assertEqual(enveloped["seq"], 7)
        self.assertEqual(enveloped["error"], "流内失败")


if __name__ == "__main__":
    unittest.main()
