"""Persist durable batch-job state through focused database modules.

The ``job_state`` package is the only database layer. ``models`` owns SQLAlchemy models and snapshots,
``database`` owns the engine and migration checks, ``batch_queue`` owns leases, claims, and fencing,
``batch_records`` owns batch CRUD and retained documents, ``child_jobs`` owns child-task state, and
``responses`` owns QuestionnaireResponse CRUD. Callers import persistence functions from the submodule
that owns them; orchestration, HTTP adapters, migrations, and upstream FHIR transport do not belong here.
"""
