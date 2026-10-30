"""Strict scalar/list Fortran namelist parsing shared by read-only validators."""
import re


def parse_namelist(text):
    """Small strict parser: unsupported syntax must fail, never silently truncate."""
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|![^\n]*|"
                        r"[&/=,*()]|[^\s&/=,*()!]+", text)
    tokens = [t for t in tokens if not t.startswith('!')]
    groups, i = {}, 0
    while i < len(tokens):
        if tokens[i] != '&':
            raise ValueError(f'Expected namelist group, found {tokens[i]!r}')
        group = tokens[i + 1].lower()
        if group in groups:
            raise ValueError(f'Duplicate group {group}; use a full namelist parser')
        values = groups[group] = {}
        i += 2
        while i < len(tokens) and tokens[i] != '/':
            if tokens[i] == ',':
                i += 1
                continue
            key = tokens[i].lower()
            if not re.fullmatch(r'[a-z][a-z0-9_]*', key) or tokens[i + 1] != '=':
                raise ValueError(f'Unsupported assignment near {tokens[i:i+4]}')
            if key in values:
                raise ValueError(f'Duplicate assignment {key}; use a full namelist parser')
            i += 2
            items = []
            comma = False
            while i < len(tokens) and tokens[i] != '/':
                if i + 1 < len(tokens) and tokens[i + 1] == '=':
                    break
                if tokens[i] == ',':
                    if comma or not items:
                        raise ValueError(f'Null list value in {key}')
                    comma = True
                    i += 1
                    continue
                repeat = 1
                if i + 1 < len(tokens) and tokens[i + 1] == '*':
                    repeat = int(tokens[i])
                    if not 0 < repeat <= 100000:
                        raise ValueError('Unsupported repetition count')
                    i += 2
                token = tokens[i]
                if token[:1] in ("'", '"'):
                    if not re.fullmatch(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", token):
                        raise ValueError(f'Unterminated quoted value in {key}')
                    value = token[1:-1].replace(token[0] * 2, token[0])
                elif token.lower() in ('.true.', '.false.'):
                    value = token.lower() == '.true.'
                elif re.fullmatch(r'[+-]?\d+', token):
                    value = int(token)
                elif re.fullmatch(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][+-]?\d+)?', token):
                    value = float(re.sub('[dD]', 'e', token))
                else:
                    raise ValueError(f'Unsupported namelist value {token!r} in {key}')
                items.extend([value] * repeat)
                comma = False
                i += 1
            if not items:
                raise ValueError(f'Empty assignment {key}')
            values[key] = items[0] if len(items) == 1 else items
        if i == len(tokens):
            raise ValueError(f'Unterminated group {group}')
        i += 1
    return groups
