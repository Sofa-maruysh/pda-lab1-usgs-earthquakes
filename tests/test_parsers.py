"""Boundary and ambiguity checks for the actual functions in the notebook.

Run: python -m unittest discover -s tests -v
Only definition cells tagged `parsers` execute; no download or analysis is needed.
"""
import json
from pathlib import Path
import re
import unittest

import numpy as np
import pandas as pd


namespace = {'re': re, 'np': np, 'pd': pd}
notebook = json.loads((Path(__file__).resolve().parents[1] / 'lab3_data_quality.ipynb').read_text(encoding='utf-8'))
for cell in notebook['cells']:
    if cell['cell_type'] == 'code' and 'parsers' in cell.get('metadata', {}).get('tags', []):
        exec(''.join(cell['source']), namespace)

parse_location = namespace['parse_location']
parse_salary = namespace['parse_salary']
parse_job_type = namespace['parse_job_type']
infer_period = namespace['infer_period']


class LocationTests(unittest.TestCase):
    def test_components_and_leading_zero(self):
        result = parse_location('  Boston,\xa0MA 02108-1234  ')
        self.assertEqual((result['city'], result['state_code'], result['postal_code']),
                         ('Boston', 'MA', '02108-1234'))

    def test_partial_formats(self):
        for text, expected in [('MA', (None, 'MA', None)), ('02108', (None, None, '02108')),
                               ('Boston 02108', ('Boston', None, '02108')),
                               ('02108 Boston', ('Boston', None, '02108')),
                               ('MA, Boston 02108', ('Boston', 'MA', '02108')),
                               ('DallasTX', ('Dallas', 'TX', None)),
                               ('Alexandria 22314, Virginia US', ('Alexandria', 'VA', '22314')),
                               ('Boston, Massachusetts 02108', ('Boston', 'MA', '02108'))]:
            with self.subTest(text=text):
                r = parse_location(text)
                self.assertEqual((r['city'], r['state_code'], r['postal_code']), expected)

    def test_unknown_text_and_phone_are_not_city(self):
        for text in ['Contact name Joe Carpenter', 'Phone 770-892-6627', 'Systems Administrator', 'Telecommute']:
            with self.subTest(text=text):
                r = parse_location(text, {'boston'})
                self.assertEqual(r['location_format'], 'Не распознан')
                self.assertIsNone(r['postal_code'])
        self.assertEqual(parse_location('Boston', {'boston'})['city'], 'Boston')

    def test_recovery_and_ambiguity(self):
        pattern = re.compile(r'(?P<city>Boston|Austin)\s*,\s*(?P<state>MA|TX)(?![A-Za-z])', re.I)
        recover = namespace['recover_location']
        r = recover('Phone 123-456-7890 Address 10 Main St.Boston, MA 02108', {'boston', 'austin'}, pattern)
        self.assertEqual((r['city'], r['postal_code']), ('Boston', '02108'))
        self.assertEqual(recover('Boston, MA or Austin, TX', {'boston', 'austin'}, pattern)['location_format'],
                         'Неоднозначная география')

    def test_address_partial_components_and_camel_boundary(self):
        pattern = re.compile(r'(?:(?<![A-Za-z])|(?<=(?-i:[a-z]))(?=(?-i:[A-Z])))'
                             r'(?P<city>Las Vegas)\s*,\s*(?P<state>NV)(?![A-Za-z])', re.I)
        recover = namespace['recover_location']
        r = recover('Address 1111 W. Bonanza RoadLas Vegas, NV', {'las vegas'}, pattern)
        self.assertEqual((r['city'], r['state_code']), ('Las Vegas', 'NV'))
        r = recover('Address 221 S Roop StreetSusanville, CA 96130', {'las vegas'}, pattern)
        self.assertEqual((r['city'], r['state_code'], r['postal_code']), (None, 'CA', '96130'))
        r = recover('Contact name Jane Phone 706-854-1220 Address 3154 Perimeter Parkway30809', set(), pattern)
        self.assertEqual(r['postal_code'], '30809')

    def test_words_in_description_are_not_state_codes(self):
        pattern = re.compile(r'(?P<city>Boston)\s*,\s*(?P<state>MA)(?![A-Za-z])', re.I)
        recover = namespace['recover_location']
        for text in ['Location: Some unknown city. Work in finance, or in sales.',
                     'Address an unknown place, or', 'Location:', 'Address']:
            with self.subTest(text=text):
                r = recover(text, {'boston'}, pattern)
                self.assertIsNone(r['state_code'])
                self.assertIsNone(r['city'])


class SalaryTests(unittest.TestCase):
    def test_range_period_and_note(self):
        r = parse_salary('  40,000.00 - 50,000.00 $ /yearBonus, 401k  ')
        self.assertEqual((r['salary_min'], r['salary_max'], r['salary_period_observed']), (40000., 50000., 'year'))
        self.assertEqual(r['salary_note'], 'Bonus, 401k')

    def test_single_amount_and_up_to_follow_assignment(self):
        for text in ['$12.00 /hour', '$12.00+ /hour', 'Up to $12.00']:
            with self.subTest(text=text):
                r = parse_salary(text)
                self.assertEqual(r['salary_min'], 12.)
                self.assertTrue(np.isnan(r['salary_max']))
        self.assertEqual(parse_salary('Up to $12.00')['salary_bound_kind'], 'up_to')

    def test_non_wage_numbers_and_reverse_range(self):
        for text in ['$300 Hiring Bonus', '401K, Medical, Dental', 'Salary based on experience', '-12 /hour']:
            with self.subTest(text=text):
                self.assertTrue(np.isnan(parse_salary(text)['salary_min']))
        self.assertEqual(parse_salary('20 - 10 /hour')['salary_format'], 'Обратные границы')

    def test_zero_and_missing_period(self):
        r = parse_salary('0 - 20 /hour')
        self.assertEqual((r['salary_min'], r['salary_max']), (0., 20.))
        self.assertIsNone(parse_salary('108,887.00 - 141,555.00')['salary_period_observed'])

    def test_all_periods(self):
        for period in ['hour', 'week', 'month', 'year']:
            self.assertEqual(parse_salary(f'$100 per {period}')['salary_period_observed'], period)

    def test_ambiguity_outlier_and_small_sample(self):
        rules = pd.DataFrame({'mean_trimmed': [20., 100.], 'lower': [10., 70.],
                              'upper': [30., 130.], 'small_sample': [False, False]}, index=['hour', 'week'])
        self.assertEqual(infer_period(21, rules)[0], 'hour')
        self.assertIsNone(infer_period(60, rules)[0])
        self.assertIsNone(infer_period(1000, rules)[0])
        self.assertIsNone(infer_period(0, rules)[0])
        rules.loc['week', 'small_sample'] = True
        self.assertIsNone(infer_period(100, rules)[0])


class EmploymentTests(unittest.TestCase):
    def test_format_variants_and_contract_category(self):
        values = ['Full Time Employee', 'Full Time, Employee', 'Full Time\xa0 Employee',
                  'Job Type Full Time / > Employee']
        for text in values:
            with self.subTest(text=text):
                r = parse_job_type(text)
                self.assertEqual((r['employment_fullness'], r['employment_type']), ('Full Time', 'Employee'))
                self.assertFalse(r['fullness_defaulted'])
                self.assertFalse(r['employment_type_defaulted'])
                self.assertIsNone(r['job_type_unrecognized'])
        self.assertEqual(parse_job_type('Part Time/ Temporary/Contract/Project')['employment_type'],
                         'Temporary/Contract/Project')

    def test_defaults_and_unrecognized_text(self):
        r = parse_job_type(pd.NA)
        self.assertEqual((r['employment_fullness'], r['employment_type']), ('Full Time', 'Employee'))
        self.assertTrue(r['fullness_defaulted'] and r['employment_type_defaulted'])
        self.assertEqual(parse_job_type('Per Diem')['employment_fullness'], 'Per Diem')
        self.assertEqual(parse_job_type('Part Time Seasonal')['employment_type'], 'Seasonal')
        self.assertEqual(parse_job_type('Exempt')['job_type_unrecognized'], 'Exempt')


if __name__ == '__main__':
    unittest.main()
