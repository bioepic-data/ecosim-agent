"""Create disjoint contiguous-year holdout folds; does not fit or run BASD."""
import argparse
import json
from pathlib import Path


def make_folds(start_year, end_year, block_years):
    if not (1 <= start_year <= end_year <= 9999):
        raise ValueError('Require 1 <= start_year <= end_year <= 9999')
    years = list(range(start_year, end_year + 1))
    if not 1 <= block_years < len(years):
        raise ValueError('block_years must be positive and leave training years')
    folds = []
    for offset in range(0, len(years), block_years):
        heldout = years[offset:offset + block_years]
        training = [y for y in years if y not in heldout]
        folds.append(dict(fold=len(folds) + 1, training_years=training,
                          validation_years=heldout))
    return dict(method='contiguous_year_block_cross_validation',
                years=years, block_years=block_years, folds=folds,
                observations_allowed_for_fit='training_years only',
                application_source_statistics='allowed if declared; no held-out observations',
                limitations=['Splits do not validate measurement coverage or fit sample sufficiency.',
                             'Not forward-only forecasting validation.',
                             'Hyperparameter selection requires training-only or nested validation.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-year', type=int, required=True)
    parser.add_argument('--end-year', type=int, required=True)
    parser.add_argument('--block-years', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = make_folds(args.start_year, args.end_year, args.block_years)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(plan, stream, indent=2, allow_nan=False)
            stream.write('\n')
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(args.output)


if __name__ == '__main__':
    main()
