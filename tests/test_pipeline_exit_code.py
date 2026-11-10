import os
import sys
sys.path.insert(0, os.path.abspath('.'))

from unittest.mock import patch

import pipeline


def _run_main(argv):
    with patch.object(sys, 'argv', argv):
        try:
            pipeline.main()
        except SystemExit as exc:
            return exc.code
        return 0


def test_failed_conversion_exits_nonzero(tmp_path, capsys):
    pdf = tmp_path / 'broken.pdf'
    pdf.write_bytes(b'%PDF-1.4 bogus content, not a real paper')
    code = _run_main(['pipeline.py', str(tmp_path)])
    assert code == 1
    out = capsys.readouterr().out
    assert 'FAILED' in out
