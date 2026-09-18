"""Generated SQL-first Hamilton Data Vault DAG."""
from vault.v2.engine import Context

def stage_crm(context: Context) -> dict:
    return context.stage('crm')

def stage_erp(context: Context) -> dict:
    return context.stage('erp')

def load_hub_customer(context: Context, stage_crm: dict, stage_erp: dict) -> dict:
    return context.load('hub_customer')

def load_hub_order(context: Context, stage_erp: dict) -> dict:
    return context.load('hub_order')

def load_link_customer_order(context: Context, stage_erp: dict, load_hub_customer: dict, load_hub_order: dict) -> dict:
    return context.load('link_customer_order')

def load_sat_customer(context: Context, stage_crm: dict, load_hub_customer: dict) -> dict:
    return context.load('sat_customer')

def load_sat_order(context: Context, stage_erp: dict, load_link_customer_order: dict) -> dict:
    return context.load('sat_order')
