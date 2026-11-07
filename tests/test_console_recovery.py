import unittest
from unittest.mock import Mock,patch
import pymysql
from appmon import console


class ConsoleRecoveryTest(unittest.TestCase):
    def test_broken_connection_does_not_poison_next_maintenance_cycle(self):
        first,second=Mock(),Mock()
        with patch('threading.Thread'),patch('appmon.db.connect',side_effect=[first,second]) as connect,\
             patch.object(console,'maintenance_once',side_effect=[pymysql.OperationalError(2013,'lost connection'),None]) as cycle,\
             patch.object(console.time,'sleep',side_effect=[None,SystemExit]),self.assertLogs(console.log,level='ERROR') as logs:
            with self.assertRaises(SystemExit):console.worker({})
        self.assertEqual(connect.call_count,2)
        self.assertEqual([call.args[1] for call in cycle.call_args_list],[first,second])
        first.close.assert_called_once();second.close.assert_called_once()
        self.assertIn('code=2013',logs.output[0])

    def test_connection_failure_also_recovers_on_next_cycle(self):
        recovered=Mock()
        with patch('threading.Thread'),patch('appmon.db.connect',side_effect=[pymysql.OperationalError(2003,'unavailable'),recovered]),\
             patch.object(console,'maintenance_once') as cycle,\
             patch.object(console.time,'sleep',side_effect=[None,SystemExit]),self.assertLogs(console.log,level='ERROR'):
            with self.assertRaises(SystemExit):console.worker({})
        cycle.assert_called_once_with({},recovered)
        recovered.close.assert_called_once()
