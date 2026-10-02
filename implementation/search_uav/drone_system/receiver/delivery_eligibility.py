import json
import re
from pathlib import Path


class JournalDeliveryEligibility:
    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else None

    def __call__(self, mission_id, execution_id, fingerprint):
        if self.path is None or not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
            return False
        try:
            with self.path.open(encoding='utf-8') as handle:
                journal = json.load(handle)
            if (not isinstance(journal, dict) or type(journal.get('journal_version')) is not int
                    or journal['journal_version'] != 1):
                return False
            record = journal['executions'][execution_id]
            return (isinstance(record, dict)
                    and record.get('mission_id') == mission_id and record.get('execution_id') == execution_id
                    and record.get('content_fingerprint') == fingerprint
                    and record.get('all_waypoints_completed') is True
                    and record.get('land_command_requested') is True
                    and record.get('delivery_eligible') is True
                    and record.get('execution_aborted') is False)
        except (OSError, ValueError, KeyError, TypeError):
            return False
