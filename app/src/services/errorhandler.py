from src.models.functions import make_operation_outcome

def error_to_operation_outcome(error: ValueError):
    print(error.args)
    print(issubclass(type(error), Exception))
    oo = make_operation_outcome("invalid", error.args[0])
    print(oo)