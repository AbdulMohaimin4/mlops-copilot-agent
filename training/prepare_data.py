"""DVC `prepare` stage: export the Breast Cancer Wisconsin dataset to a versioned CSV.

The sample fraction comes from `params.yaml`, so each dataset version is fully
determined by git (params + code) and reproducible with `dvc repro`.
"""

from pathlib import Path

import pandas as pd
import yaml
from sklearn.datasets import load_breast_cancer

DATA_PATH = Path("data/breast_cancer.csv")
PARAMS_PATH = Path("params.yaml")


def build_dataset(sample_frac: float, seed: int) -> pd.DataFrame:
    """Return the dataset with snake_case columns, deterministically sampled."""
    frame = load_breast_cancer(as_frame=True).frame
    frame.columns = [column.replace(" ", "_") for column in frame.columns]
    return frame.sample(frac=sample_frac, random_state=seed).sort_index()


def main() -> None:
    params = yaml.safe_load(PARAMS_PATH.read_text())["data"]
    dataset = build_dataset(params["sample_frac"], params["seed"])
    DATA_PATH.parent.mkdir(exist_ok=True)
    dataset.to_csv(DATA_PATH, index=False)
    print(f"wrote {len(dataset)} rows to {DATA_PATH}")


if __name__ == "__main__":
    main()
