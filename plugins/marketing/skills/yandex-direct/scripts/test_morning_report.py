"""Тесты утреннего отчёта: python -m unittest test_morning_report (из папки scripts)."""
import unittest

import morning_report as mr


class AddTest(unittest.TestCase):
    def test_add_skips_text_fields_of_query_row(self):
        # строка поискового запроса хранит рядом с цифрами текст: 20.11.2026 отчёт падал на ней с TypeError
        q = {'query': 'аренда 3d витрины', 'campaign': 'Выставки', 'cid': 1, 'criterion': '', 'cat': '',
             'yesterday': False, **mr.blank()}
        mr.add(q, {'impr': 5, 'clicks': 1, 'cost': 120.5, 'leads': 0, 'contacts': 0})
        mr.add(q, {'impr': 3, 'clicks': 0, 'cost': 0.0, 'leads': 1, 'contacts': 0})
        self.assertEqual((q['impr'], q['clicks'], q['cost'], q['leads']), (8, 1, 120.5, 1))
        self.assertEqual(q['query'], 'аренда 3d витрины')


if __name__ == '__main__':
    unittest.main()
