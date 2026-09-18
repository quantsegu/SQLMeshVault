"""Generated Hamilton DAG. Regenerate after metadata changes."""
from vault.runtime import Context

def stage_sales(context: Context) -> list:
    return context.stage('sales')

def load_hub_customer(context: Context, stage_sales: list) -> dict:
    return context.load('hub_customer', stage_sales)

def load_hub_order(context: Context, stage_sales: list) -> dict:
    return context.load('hub_order', stage_sales)

def load_link_customer_order(context: Context, stage_sales: list, load_hub_customer: dict, load_hub_order: dict) -> dict:
    return context.load('link_customer_order', stage_sales)

def load_sat_customer(context: Context, stage_sales: list, load_hub_customer: dict) -> dict:
    return context.load('sat_customer', stage_sales)

def load_sat_order(context: Context, stage_sales: list, load_link_customer_order: dict) -> dict:
    return context.load('sat_order', stage_sales)
