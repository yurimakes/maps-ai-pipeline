import argparse
import json

from .contracts import RequestConflict, StorageError, ValidationError
from .pipeline import run


def main():
    parser = argparse.ArgumentParser(description="Local synthetic dictionary recommendation")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = run(args.request, args.output)
    except RequestConflict:
        print(json.dumps({"status": "request_conflict"}))
        return 3
    except (ValidationError, StorageError, OSError):
        print(json.dumps({"status": "validation_or_storage_error"}))
        return 2
    columns = result["envelope"]["result"]["columns"]
    print(json.dumps({"status": "reused" if result["reused"] else "saved",
                      "columns": len(columns),
                      "term_candidate_columns": sum(c["recommendation_status"] == "candidate" for c in columns),
                      "mapping_candidate_columns": sum(bool(c["mapping_candidates"]) for c in columns),
                      "model_used": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
