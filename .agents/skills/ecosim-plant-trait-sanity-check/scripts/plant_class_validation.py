"""Model-domain and evidence validation of every PLANT CLASS INFORMATION item.

Codes/printed labels verified against ElmIDMod.F90 and PlantInfoMod.F90,
2026-11-16. Ecological support comes from explicit cited rules, not code validity.
"""
from __future__ import annotations

import math
import re
from pathlib import Path


CLASS_SECTION = 'PLANT CLASS INFORMATION'
CLASS_CODES = {
    'ICTYP': {3: 'C3', 4: 'C4'},
    'IEBTYP': {0: 'bryophyte', 1: 'pteridophyte', 2: 'gymnosperm', 3: 'monocot', 4: 'eudicot'},
    'IGTYP': {0: 'shallow', 1: 'intermediate', 2: 'deep penetrative', 3: 'very deep'},
    'ISTYP': {0: 'annual', 1: 'perennial'},
    'IDTYP': {0: 'determinate', 1: 'indeterminate'},
    'INTYP': {0: 'no fixation', 1: 'rapid root', 2: 'intermediate root', 3: 'slow root',
              4: 'rapid canopy', 5: 'intermediate canopy', 6: 'slow canopy'},
    'IWTYP': {0: 'evergreen', 1: 'cold deciduous', 2: 'drought deciduous', 3: 'cold and drought deciduous'},
    'IPTYP': {0: 'neutral day', 1: 'short day', 2: 'long day'},
    'IBTYP': {0: 'rapid all aboveground', 1: 'rapid foliar', 2: 'very slow tree',
              3: 'slow turnover', 4: 'semi-deciduous', 5: 'semi-evergreen'},
    'I2EXP': {0: 'no', 1: 'yes'},
    'ISNTYP': {0: 'bryophyte', 1: 'grass', 2: 'shrub', 3: 'deciduous tree', 4: 'conifer'},
    'IRTYP': {0: 'aboveground', 1: 'belowground'},
    'MY': {1: 'no', 2: 'yes'},
    'ZTYPI': None,  # Continuous range [0,5], not an integer enum.
}
THERMAL_FIELDS = ('RG_ELOW', 'RG_EHIGH', 'CG_ELOW', 'CG_EHIGH', 'LG_ELOW', 'LG_EHIGH')
NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?'


def norm(value):
    """Normalize labels without confusing substrings such as determinate/indeterminate."""
    return re.sub(r'[^a-z0-9]+', ' ', str(value).lower()).strip()


def label_codes(variable, raw):
    """Decode the current printer's labels, including truncated and ambiguous labels."""
    text = norm(raw)
    if variable == 'ICTYP':
        return {k for k in (3, 4) if re.search(rf'\bc{k}\b', text)}
    if variable == 'IEBTYP':
        return {k for k, label in CLASS_CODES[variable].items() if label in text}
    if variable == 'IGTYP':
        return next(({code} for label, code in [('very deep', 3), ('deep penetrative', 2),
                     ('intermediate', 1), ('shallow', 0)] if text.startswith(label)), set())
    if variable in ('ISTYP', 'IDTYP', 'IPTYP', 'IRTYP'):
        return {k for k, label in CLASS_CODES[variable].items() if re.search(r'\b'+label+r'\b', text)}
    if variable == 'INTYP':
        if text in ('none', 'no', 'no fixation', 'no n fixation', 'non fixing'):
            return {0}
        return {k for k, label in CLASS_CODES[variable].items() if k and label in text}
    if variable == 'IWTYP':
        if text.startswith(('cold drought', 'cold and drought')): return {3}
        return next(({code} for label, code in [('cold deciduous', 1), ('drought deciduous', 2),
                     ('evergreen', 0)] if text.startswith(label)), set())
    if variable == 'IBTYP':
        if text.startswith('rapid'): return {0, 1}  # Printer conflates these.
        if text.startswith('very slow like evergreen grass'): return {3}
        if text.startswith('very slow'): return {2}
        if text.startswith('slow'): return {3}
        if text.startswith('like semi deciduous'): return {4}
        if text.startswith('like semi evergreen'): return {5}
        return set()
    if variable in ('I2EXP', 'MY'):
        choices = CLASS_CODES[variable]
        return {k for k, label in choices.items() if re.match(label+r'\b', text)}
    if variable == 'ISNTYP':
        for tokens, code in [(('bryophyte', 'moss', 'lichen'), 0), (('grasse', 'grass', 'graminoid'), 1),
                             (('shrub',), 2), (('deciduous tree',), 3), (('conifer', 'confier'), 4)]:
            if any(t in text for t in tokens): return {code}
        return set()
    if variable == 'ZTYPI':
        return next(({code} for label, code in [('arctic', 1), ('boreal', 1), ('cool temperate', 2),
                     ('warm temperate', 3), ('subtropical', 4), ('tropical', 5)] if text.startswith(label)), set())
    return set()


def valid_code(variable, value):
    """Check actual model domains; MY's no/yes codes are 1/2, not 0/1."""
    if not math.isfinite(value): return False
    if variable == 'ZTYPI': return 0 <= value <= 5
    return value == int(value) and int(value) in CLASS_CODES[variable]


def decode(variable, parameter, readback=None):
    """Return a model-domain verdict without inferring a code from descriptive digits."""
    raw = parameter.raw_value.strip()
    labels = label_codes(variable, raw)
    messages = []
    if re.search(r'\b(?:nan|[+-]?inf(?:inity)?)\b', raw, re.I):
        return None, 'invalid', ['non-finite class value']
    # C3/C4 labels contain a digit; only a separate terminal number is a code.
    numeric = re.search(r'(?:^|[\s(])('+NUMBER+r')\)?\s*$', raw)
    printed = float(re.sub('[dD]', 'e', numeric[1])) if numeric else None
    value = readback if readback is not None else printed
    if printed is not None and not valid_code(variable, printed):
        messages.append(f'printed code {printed:g} is outside the model domain')
    # Both old "Not defined" and the new "No N-fixation" default can also
    # represent an unsupported INTYP. Only numeric readback disambiguates it.
    lossy_nonfixer = variable == 'INTYP' and norm(raw) in ('not defined', 'no n fixation')
    if lossy_nonfixer and readback is None and printed is None:
        return None, 'unresolved', ['non-fixation label requires --pft-file or a printed numeric code to confirm INTYP=0']
    if value is None and len(labels) == 1:
        # A zone label alone cannot establish a continuous thermal value.
        if variable != 'ZTYPI': value = float(next(iter(labels)))
    if readback is not None and printed is not None and abs(readback-printed) > 1e-6:
        messages.append(f'printed code {printed:g} disagrees with PFT NetCDF value {readback:g}')
    if value is not None and not valid_code(variable, value):
        messages.append(f'value {value:g} is outside the model domain')
    if value is not None and labels:
        bucket = max(1, math.ceil(value)) if variable == 'ZTYPI' and math.isfinite(value) else value
        if bucket not in labels: messages.append('class label disagrees with numeric code')
    # INTYP=0 is valid but PlantInfoMod currently prints it as Not defined.
    if not labels and not numeric and not (lossy_nonfixer and value == 0):
        if lossy_nonfixer and readback is None:
            return None, 'unresolved', ['INTYP label is lossy: supply --pft-file to distinguish valid non-fixing code0 from an invalid code']
        messages.append('unrecognized or undefined class label')
    if messages: return value, 'invalid', messages
    if not labels and numeric and re.sub(r'[\s()]+', '', raw[:numeric.start()]).strip():
        if not (lossy_nonfixer and value == 0):
            return value, 'invalid', ['unrecognized or undefined label accompanying numeric code']
    if value is None: return None, 'unresolved', ['ambiguous class label; provide numeric PFT readback']
    return value, 'valid', []


def constraint_match(record, rule):
    """Evaluate a nonempty categorical or numeric evidence condition; None means unusable."""
    raw = record['raw_value']; value = record['value']; variable = record['variable']
    tests = []
    codes = rule.get('expected_codes')
    if codes is not None:
        if not isinstance(codes, list) or not codes or value is None: return None
        try:
            codes = [float(c) for c in codes]
            if not all(valid_code(variable, c) for c in codes): return None
            tests.append(any(abs(value-c) < 1e-6 for c in codes))
        except (TypeError, ValueError): return None
    contains = rule.get('contains', rule.get('expected_contains'))
    if isinstance(contains, str): contains = [contains]
    if contains is not None:
        if not isinstance(contains, list) or not contains or not all(str(x).strip() for x in contains): return None
        tests.append(all(re.search(r'(?<![a-z0-9])'+re.escape(norm(x))+r'(?![a-z0-9])',norm(raw)) is not None for x in contains))
    allowed = rule.get('allowed_values')
    if allowed is not None:
        if not isinstance(allowed, list) or not allowed: return None
        tests.append(norm(raw) in [norm(x) for x in allowed])
    if variable == 'ZTYPI' and any(k in rule for k in ('min', 'max')):
        if value is None: return None
        try:
            lo, hi = float(rule.get('min', 0)), float(rule.get('max', 5))
            if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= hi <= 5): return None
            tests.append(lo <= value <= hi)
        except (TypeError, ValueError): return None
    return all(tests) if tests else None


def evidence_review(record, rules):
    """Do not turn omitted, uncited, no-op, or declared-unresolved evidence into a pass."""
    reviews=[]
    for rule in rules:
        if rule.get('variable') != record['variable']: continue
        match = constraint_match(record, rule)
        source = str(rule.get('source', '')).strip()
        url = str(rule.get('url', '')).strip()
        local = rule.get('source_path')
        cited = bool(source and (url.startswith(('http://', 'https://')) or
                     (isinstance(local, (str, Path)) and Path(local).is_file())))
        eligible = (rule.get('search_status', 'searched_found') == 'searched_found'
                    and bool(rule.get('evidence_level'))
                    and rule.get('evidence_level') not in ('user-default', 'model-prior', 'user-prescribed')
                    and rule.get('conclusion') not in ('unresolved', 'provisional', 'not_applicable'))
        status = ('supported' if match else 'contradicted') if match is not None and cited and eligible else 'unresolved'
        reviews.append({'status':status,'matches':match,'source':source,'url':url,'source_path':local,
                        'evidence_level':rule.get('evidence_level'),'mapping_note':rule.get('mapping_note'),
                        'reason':rule.get('reason'), 'condition':{k:rule[k] for k in
                        ('expected_codes','contains','expected_contains','allowed_values','min','max') if k in rule}})
    states={r['status'] for r in reviews}
    # Unresolved/conflicting matching rules cannot be concealed by a second passing rule.
    status = 'contradicted' if 'contradicted' in states else ('supported' if states == {'supported'} else 'unresolved')
    return status, reviews


def class_records(block, rules=(), pft_values=None):
    """Produce one explicit validation record per required field, plus unexpected fields."""
    parameters=[p for p in block.parameters if p.section == CLASS_SECTION]
    records=[]
    for variable in CLASS_CODES:
        found=[p for p in parameters if p.variable == variable]
        record={'variable':variable,'line':found[0].line if found else block.start_line,
                'raw_value':found[0].raw_value if found else '', 'value':None,
                'model_status':'invalid','messages':[],
                'allowed_codes':list(CLASS_CODES[variable]) if CLASS_CODES[variable] else 'continuous [0,5]'}
        if len(found) != 1:
            record['messages']=[f'expected exactly one {variable} row, found {len(found)}']
        else:
            readback=None if pft_values is None else pft_values.get(variable)
            record['value'],record['model_status'],record['messages']=decode(variable,found[0],readback)
        record['evidence_status'],record['evidence']=evidence_review(record,rules)
        record['status']=('invalid' if record['model_status']=='invalid' else
                          'contradicted' if record['evidence_status']=='contradicted' else
                          'validated' if record['model_status']=='valid' and record['evidence_status']=='supported' else 'unresolved')
        records.append(record)
    by_name={r['variable']:r for r in records}
    root, secondary, turnover=(by_name[k] for k in ('IGTYP','I2EXP','IBTYP'))
    if all(r['model_status']=='valid' for r in (root,secondary,turnover)):
        woody=root['value']>1 and secondary['value']==1
        if not woody and turnover['value'] not in (0,1,3):
            turnover['model_status']=turnover['status']='invalid'
            turnover['messages'].append('IBTYP is unsupported for the non-woody IGTYP/I2EXP combination; printer supports0,1,3')
    for p in parameters:
        if p.variable not in CLASS_CODES:
            records.append({'variable':p.variable,'line':p.line,'raw_value':p.raw_value,'value':None,
                            'model_status':'unresolved','evidence_status':'unresolved','evidence':[],
                            'messages':['class item has no implemented validation contract'],'status':'unresolved'})
    return records


def load_pft_values(path, blocks):
    """Read only the selected table codes. A current table is not executable provenance."""
    from netCDF4 import Dataset,chartostring
    values={}
    with Dataset(path) as ds:
        codes=[str(x).strip() for x in chartostring(ds['pfts'][:])]
        for block in blocks:
            if codes.count(block.code) != 1: raise ValueError(f'{block.code}: PFT code absent or duplicated in selected table')
            i=codes.index(block.code); row={}
            for key in CLASS_CODES:
                if key not in ds.variables: raise ValueError(f'{block.code}: selected PFT file lacks {key}')
                value=ds[key][i]
                if getattr(value, 'mask', False): raise ValueError(f'{block.code} {key}: missing PFT value')
                row[key]=float(value)
            values[block.code]=row
    return values
