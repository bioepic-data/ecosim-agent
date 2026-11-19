"""Every printed plant classification must be validated or explicitly unresolved."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_physiological_parameterization import CHECKER
from plant_class_validation import CLASS_CODES, CLASS_SECTION, class_records, decode


VALUES = {'ICTYP':'C3', 'IEBTYP':'Eudicots', 'IGTYP':'Deep penetrative root profile, like trees/drought tolerant g',
          'ISTYP':'Annual','IDTYP':'Indeterminate','INTYP':'Rapid root N-fixation','IWTYP':'Evergreen 0',
          'IPTYP':'Long day','IBTYP':'Rapid, like deciduous trees 0','I2EXP':'Yes (1)',
          'ISNTYP':'grasse 1','IRTYP':'Aboveground 0','MY':'YES','ZTYPI':'Cool temperate 2.00'}


def block():
    b=CHECKER.PlantBlock(nz=1,ny=1,nx=1,code='soyb41',start_line=1)
    b.parameters=[CHECKER.parse_parameter_line(f'{i}| {v}: description:{value}',CLASS_SECTION,i+2)
                  for i,(v,value) in enumerate(VALUES.items(),1)]
    b.sections={s:[] for s in CHECKER.REQUIRED_SECTIONS}
    b.sections[CLASS_SECTION]=b.parameters[:]
    return b


def evidence(b):
    rules=[]
    for r in class_records(b):
        rules.append({'variable':r['variable'],'expected_codes':[r['value']],
                      'source':'Test evidence fixture, not a biological citation','url':'https://example.org/fixture',
                      'evidence_level':'species','mapping_note':'Controlled fixture for evidence-validation tests'})
    return {'plants':[{'pft_code':b.code,'categorical_expectations':rules}]}


class ClassInformationTests(unittest.TestCase):
    def test_all_fourteen_receive_model_and_evidence_verdicts(self):
        rows=class_records(block())
        self.assertEqual(len(rows),14)
        self.assertEqual({r['model_status'] for r in rows},{'valid'})
        self.assertEqual({r['status'] for r in rows},{'unresolved'})

    def test_each_missing_or_duplicated_class_item_is_invalid(self):
        for variable in CLASS_CODES:
            for duplicate in (False,True):
                with self.subTest(variable=variable,duplicate=duplicate):
                    b=block();p=next(p for p in b.parameters if p.variable==variable)
                    if duplicate:b.parameters.append(copy.deepcopy(p))
                    else:b.parameters.remove(p)
                    row=next(r for r in class_records(b) if r['variable']==variable)
                    self.assertEqual(row['model_status'],'invalid')

    def test_every_class_rejects_out_of_domain_numeric_values(self):
        for variable in CLASS_CODES:
            with self.subTest(variable=variable):
                p=CHECKER.parse_parameter_line(f'1| {variable}: x:999',CLASS_SECTION,1)
                self.assertEqual(decode(variable,p)[1],'invalid')

    def test_physiology_warning_cannot_downgrade_invalid_class_encoding(self):
        b=block();next(p for p in b.parameters if p.variable=='ICTYP').raw_value='Not defined'
        CHECKER.check_blocks([b])
        self.assertEqual(b.class_validation[0]['status'],'invalid')

    def test_invalid_codes_labels_and_label_code_conflicts(self):
        cases={'IPTYP':['4','Short day 2','not defined','Long day 1.5'],
               'I2EXP':['Yes (0)','3','unknown'], 'MY':['YES 1','0','2.5'],
               'ISTYP':['Annual 1'], 'IDTYP':['Determinate 1'],
               'ZTYPI':['Cool temperate 3','nan','6','-0.1']}
        for variable,values in cases.items():
            for raw in values:
                with self.subTest(variable=variable,raw=raw):
                    p=CHECKER.parse_parameter_line(f'1| {variable}: x:{raw}',CLASS_SECTION,1)
                    self.assertEqual(decode(variable,p)[1],'invalid')

    def test_continuous_zone_and_distinct_yes_no_encodings(self):
        for variable,raw,value in [('ZTYPI','Warm temperate 2.5',2.5),('ZTYPI','Arctic, boreal 0.5',.5),
                                  ('I2EXP','YES',1),('MY','YES',2),('MY','No',1),('I2EXP','No',0)]:
            p=CHECKER.parse_parameter_line(f'1| {variable}: x:{raw}',CLASS_SECTION,1)
            actual,status,_=decode(variable,p)
            self.assertEqual((actual,status),(value,'valid'))

    def test_lossy_nonfixation_label_requires_pft_readback(self):
        p=CHECKER.parse_parameter_line('1| INTYP: x:Not defined',CLASS_SECTION,1)
        self.assertEqual(decode('INTYP',p)[1],'unresolved')
        self.assertEqual(decode('INTYP',p,0)[:2],(0,'valid'))
        self.assertEqual(decode('INTYP',p,99)[1],'invalid')

    def test_numeric_pft_readback_cannot_conceal_label_mismatch(self):
        p=CHECKER.parse_parameter_line('1| IPTYP: x:Long day',CLASS_SECTION,1)
        self.assertEqual(decode('IPTYP',p,1)[1],'invalid')

    def test_no_evidence_never_counts_as_validation(self):
        b=block();found=CHECKER.check_blocks([b],require_class_evidence=True)
        f=[x for x in found if x.category=='class-evidence']
        self.assertTrue(any(x.severity=='ERROR' and 'IPTYP' in x.message for x in f))
        self.assertEqual(len(b.class_validation),14)

    def test_complete_cited_expectations_validate_all_items(self):
        b=block();CHECKER.check_blocks([b],evidence(b),require_class_evidence=True)
        self.assertEqual({r['status'] for r in b.class_validation},{'validated'})

    def test_soybean_long_day_contradiction_is_detected(self):
        b=block();rules=evidence(b)
        next(r for r in rules['plants'][0]['categorical_expectations'] if r['variable']=='IPTYP')['expected_codes']=[1]
        found=CHECKER.check_blocks([b],rules,require_class_evidence=True)
        self.assertTrue(any(f.parameter=='IPTYP' and f.severity=='ERROR' and f.category=='class-evidence' for f in found))

    def test_direct_evidence_api_also_checks_decoded_codes(self):
        b=block();rules={'plants':[{'pft_code':b.code,'categorical_expectations':[{'variable':'IPTYP','expected_codes':[1]}]}]}
        found=CHECKER.check_web_evidence([b],rules)
        self.assertEqual([x.parameter for x in found],['IPTYP'])

    def test_missing_citation_noop_prior_and_unresolved_rules_do_not_validate(self):
        for change in [dict(url=''),dict(source=''),dict(expected_codes=[]),dict(evidence_level='model-prior'),
                       dict(search_status='not_searched'),dict(conclusion='unresolved'),dict(evidence_level='')]:
            b=block();rules=evidence(b);r=rules['plants'][0]['categorical_expectations'][0];r.update(change)
            CHECKER.check_blocks([b],rules)
            self.assertEqual(b.class_validation[0]['status'],'unresolved',change)

    def test_conflicting_evidence_is_not_hidden_by_a_passing_rule(self):
        b=block();rules=evidence(b);r=copy.deepcopy(rules['plants'][0]['categorical_expectations'][0]);r['expected_codes']=[4]
        rules['plants'][0]['categorical_expectations'].append(r);CHECKER.check_blocks([b],rules)
        self.assertEqual(b.class_validation[0]['status'],'contradicted')

    def test_passing_evidence_does_not_hide_builtin_taxonomic_conflict(self):
        b=block();p=next(p for p in b.parameters if p.variable=='IEBTYP')
        p.raw_value='Gymnosperms';p.numeric_values=[]
        rules=evidence(b);CHECKER.check_blocks([b],rules,require_class_evidence=True)
        row=next(r for r in b.class_validation if r['variable']=='IEBTYP')
        self.assertEqual(row['status'],'contradicted')

    def test_new_nonfixation_label_is_supported_with_numeric_readback(self):
        p=CHECKER.parse_parameter_line('1| INTYP: x:No N-fixation',CLASS_SECTION,1)
        self.assertEqual(decode('INTYP',p,0)[:2],(0,'valid'))
        self.assertEqual(decode('INTYP',p)[1],'unresolved')
        self.assertEqual(decode('INTYP',p,99)[1],'invalid')

    def test_substring_does_not_validate_indeterminate_as_determinate(self):
        b=block();rules=evidence(b);r=next(r for r in rules['plants'][0]['categorical_expectations'] if r['variable']=='IDTYP')
        r.pop('expected_codes');r['contains']='Determinate'
        CHECKER.check_blocks([b],rules)
        self.assertEqual(next(r for r in b.class_validation if r['variable']=='IDTYP')['status'],'contradicted')

    def test_turnover_domain_depends_on_root_and_secondary_growth_codes(self):
        b=block()
        for p in b.parameters:
            if p.variable=='I2EXP':p.raw_value='No (0)'
            if p.variable=='IBTYP':p.raw_value='Very slow, like evergreen trees 2'
        row=next(r for r in class_records(b) if r['variable']=='IBTYP')
        self.assertEqual(row['model_status'],'invalid')

    def test_thermal_and_future_sections_do_not_leak_into_classes(self):
        text='PLANT traits for FUNCTIONAL TYPE (NZ,NY,NX)= 1 1 1 soyb41\nPLANT CLASS INFORMATION\n1| ICTYP: x:C3\nTHERMAL PROPERTIES\n1: RG_ELOW | energy:NaN\nFUTURE PROPERTIES\n1: NEW | value:3\n'
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'parser_fixture.txt';path.write_text(text);b=CHECKER.parse_desc(path)[0]
        self.assertEqual([p.variable for p in b.sections[CLASS_SECTION]],['ICTYP'])
        f=CHECKER.check_blocks([b])
        self.assertTrue(any(x.parameter=='RG_ELOW' and x.severity=='ERROR' for x in f))
        self.assertTrue(any(x.parameter=='FUTURE PROPERTIES' for x in f))

    def test_json_and_markdown_expose_every_class_verdict(self):
        b=block();f=CHECKER.check_blocks([b])
        result=CHECKER.build_json(Path('read-only'),[b],[b],f,1,1,None,False)
        self.assertEqual(len(result['plants'][0]['class_information_validation']),14)
        rendered=CHECKER.render_markdown(Path('read-only'),[b],[b],f,1,1,None,False)
        for variable in CLASS_CODES:self.assertIn('| '+variable+' |',rendered)


if __name__=='__main__':unittest.main()
