Week analytics portfolio: Duka Electronics and the mobile-money agent network

Two client engagements in one repository.

Engagement	Status
2 · Mobile-money agent network (audit, clean, merge, summarise)	Complete
1 · Duka Electronics (half-year sales exploration)	Pending: the Duka generator from the Practicals section was not available when this was built (see below)
Engagement 2 · Mobile-money agent network

The legacy transaction extract (612 rows) was audited against six data-quality dimensions, cleaned by a reusable function, merged with the 20-agent register and summarised by county and by week.

Agent data before and after cleaning: 612 raw rows -> 600 clean rows (12 duplicate txn_id rows removed).

Key findings (details in the Data Quality Report at the end of agent_cleanup.ipynb):

12 duplicate transactions (11 exact copies, 1 whose copy had an unregistered agent_id).
txn_type had 5 spellings across 384 raw rows; now exactly Deposit / Withdrawal.
20 unique transactions had a missing amount (blank or N/A); imputed with the per-type median and flagged amount_imputed.
5 transactions of exactly KES 250,000 (8.7x the next-largest amount, across 5 agents) are flagged and held out of totals pending verification against the source ledger.
7 transactions reference agent AG099, which is not in the register; kept, county Unknown, agent_known = False.
Engagement 1 · Duka Electronics

generate_data.py has a marked slot for the Duka generator (seed 2026, writes duka_sales.csv). Once it is pasted in unchanged, the following still need to be built: duka_sales_exploration.ipynb, excel/duka_branch_summary.xlsx and outputs/premium_online_coast_capital.csv.

Duka Executive Summary

Pending: this has to be written from the Duka results, so it is not included yet.

Repository layout
Path	Purpose
generate_data.py	Dataset generators (agent network, seed 44, is included; Duka slot pending)
cleaning_pipeline.py	clean_agent_data(txns, agents) -> (clean_df, log) and audit_agent_data(...)
agent_cleanup.ipynb	Engagement 2 notebook, ending with the Data Quality Report
outputs/agent_transactions_clean.csv	Cleaned, merged transactions
agents.csv, agent_transactions.csv	Generated inputs
requirements.txt	Libraries used
How to regenerate the data and run the notebooks
bash
pip install -r requirements.txt
python generate_data.py          # writes agents.csv, agent_transactions.csv (and duka_sales.csv once added)
jupyter lab                      # run from the repository root so imports and paths resolve

Open agent_cleanup.ipynb and choose Run > Run All Cells. It imports cleaning_pipeline.py, so keep both in the repository root. The notebook rewrites outputs/agent_transactions_clean.csv.
