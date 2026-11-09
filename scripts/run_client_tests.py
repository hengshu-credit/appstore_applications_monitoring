"""Windows portable client tests; full OceanBase/S3 integration runs on Linux."""
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests'))
from test_monitor import OceanBaseTestCase

def leaves(suite):
    for test in suite:
        if isinstance(test,unittest.TestSuite):yield from leaves(test)
        else:yield test

tests=unittest.defaultTestLoader.discover(str(root/'tests'))
selected=unittest.TestSuite(t for t in leaves(tests) if not isinstance(t,OceanBaseTestCase))
if selected.countTestCases()==0:raise RuntimeError('No portable tests discovered')
result=unittest.TextTestRunner(verbosity=2).run(selected)
sys.exit(not result.wasSuccessful())
