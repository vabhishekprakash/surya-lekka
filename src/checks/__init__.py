from .arithmetic import check_gross_total, check_net_cost
from .capacity import check_capacity


def run_checks(quote):
    return [check_capacity(quote), check_gross_total(quote), check_net_cost(quote)]
