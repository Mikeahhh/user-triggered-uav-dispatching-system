import itertools
import json
import tempfile
import unittest
from pathlib import Path

from delivery_eligibility import JournalDeliveryEligibility


class DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'execution.json'
        self.gate = JournalDeliveryEligibility(self.path)
        self.record = dict(mission_id='user/event', execution_id='exec-a', content_fingerprint='a' * 64,
                           all_waypoints_completed=True, land_command_requested=True,
                           delivery_eligible=True, execution_aborted=False)

    def write(self, record=None):
        self.path.write_text(json.dumps(dict(journal_version=1,
                                            executions={'exec-a': self.record if record is None else record})))

    def allowed(self):
        return self.gate('user/event', 'exec-a', 'a' * 64)

    def test_every_completion_and_abort_combination(self):
        for completed, land, eligible, aborted in itertools.product((False, True), repeat=4):
            with self.subTest(completed=completed, land=land, eligible=eligible, aborted=aborted):
                self.write(dict(self.record, all_waypoints_completed=completed,
                                land_command_requested=land, delivery_eligible=eligible,
                                execution_aborted=aborted))
                self.assertEqual(self.allowed(), completed and land and eligible and not aborted)

    def test_malformed_or_unreadable_evidence_withholds_records(self):
        for raw in ('null', '[]', '0', 'true', '"text"', '{', '{"journal_version":1,"executions":{"exec-a":[]}}',
                    '{"journal_version":1,"executions":{"exec-a":null}}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw)
                self.assertFalse(self.allowed())
        self.path.unlink()
        self.assertFalse(self.allowed())
        self.path.mkdir()
        self.assertFalse(self.allowed())

    def test_ambiguous_legacy_or_non_boolean_facts_are_not_completion(self):
        for field in ('all_waypoints_completed', 'land_command_requested', 'delivery_eligible', 'execution_aborted'):
            for value in (None, 0, 1, 'true', 'false', [], {}):
                with self.subTest(field=field, value=value):
                    self.write(dict(self.record, **{field: value}))
                    self.assertFalse(self.allowed())
            missing = dict(self.record)
            missing.pop(field)
            self.write(missing)
            self.assertFalse(self.allowed())

    def test_completion_requires_all_three_identities(self):
        for field, value in (('mission_id', 'other/event'), ('execution_id', 'exec-b'),
                             ('content_fingerprint', 'b' * 64)):
            self.write(dict(self.record, **{field: value}))
            self.assertFalse(self.allowed())
        self.write()
        self.assertTrue(self.allowed())


if __name__ == '__main__':
    unittest.main()
