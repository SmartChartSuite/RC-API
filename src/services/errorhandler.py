"""Error handling module"""

from fhir.resources.R4B.operationoutcome import OperationOutcome


def error_to_operation_outcome(error: ValueError):
    """Converting error to OperationOutcome"""
    print(error.args)
    print(issubclass(type(error), Exception))
    operation_outcome = make_operation_outcome("invalid", error.args[0])
    print(operation_outcome)


def make_operation_outcome(code: str, diagnostics: str, severity: str = "error") -> dict:
    """Returns an OperationOutcome for a given code, diagnostics string, and a severity (Default of error)"""
    oo_template: dict[str, list[dict[str, str]]] = {
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ]
    }
    return OperationOutcome(**oo_template).model_dump() #type: ignore
