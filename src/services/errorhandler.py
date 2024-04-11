"""Error handling module"""

from src.models.functions import make_operation_outcome


def error_to_operation_outcome(error: ValueError):
    """Converting error to OperationOutcome"""
    print(error.args)
    print(issubclass(type(error), Exception))
    operation_outcome = make_operation_outcome("invalid", error.args[0])
    print(operation_outcome)
