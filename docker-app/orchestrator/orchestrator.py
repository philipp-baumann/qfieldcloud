import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from qfieldcloud.core.models import ApplyJob, Delta, ExportJob, Job

from .db_utils import use_test_db_if_exists
from .docker_utils import run_docker

logger = logging.getLogger(__name__)

TMP_DIRECTORY = os.environ.get("TMP_DIRECTORY", None)

assert TMP_DIRECTORY


class QgisException(Exception):
    pass


def export_project(job_id, project_file):
    """Start a QGIS docker container to export the project using libqfieldsync """

    logger.info(f"Starting a new export for project {job_id}")

    with use_test_db_if_exists():
        try:
            job = ExportJob.objects.get(id=job_id)
        except ExportJob.DoesNotExist:
            logger.warning(f"ExportJob {job_id} does not exist.")
            return -1, f"ExportJob {job_id} does not exist."

        project_id = job.project_id
        orchestrator_tempdir = tempfile.mkdtemp(dir="/tmp")
        qgis_tempdir = Path(TMP_DIRECTORY).joinpath(orchestrator_tempdir)

        job.status = Job.Status.STARTED
        job.save()

        exit_code, output = run_docker(
            f"xvfb-run python3 entrypoint.py export {project_id} {project_file}",
            volumes={qgis_tempdir: {"bind": "/io/", "mode": "rw"}},
        )

        logger.info(
            "export_project, projectid: {}, project_file: {}, exit_code: {}, output:\n\n{}".format(
                project_id, project_file, exit_code, output.decode("utf-8")
            )
        )

        if not exit_code == 0:
            job.status = Job.Status.FAILED
            job.output = output.decode("utf-8")
            job.save()
            raise QgisException(output)

        exportlog_file = os.path.join(orchestrator_tempdir, "exportlog.json")

        try:
            with open(exportlog_file, "r") as f:
                exportlog = json.load(f)
        except FileNotFoundError:
            exportlog = "Export log not available"

        job.status = Job.Status.FINISHED
        job.output = output.decode("utf-8")
        job.exportlog = exportlog
        job.save()

        return exit_code, output.decode("utf-8"), exportlog


def apply_deltas(job_id, project_file):
    """Start a QGIS docker container to apply a deltafile using the
    apply-delta script"""

    logger.info(f"Starting a new delta apply job {job_id}")

    with use_test_db_if_exists():
        try:
            job = ApplyJob.objects.get(id=job_id)
        except ApplyJob.DoesNotExist:
            logger.warning(f"ApplyJob {job_id} does not exist.")
            return -1, f"ApplyJob {job_id} does not exist."

        project_id = job.project_id
        overwrite_conflicts = job.overwrite_conflicts
        orchestrator_tempdir = tempfile.mkdtemp(dir="/tmp")
        qgis_tempdir = Path(TMP_DIRECTORY).joinpath(orchestrator_tempdir)

        deltas = job.deltas_to_apply.all()
        deltas.update(last_status=Delta.Status.STARTED)

        json_content = {
            "deltas": [delta.content for delta in deltas],
            "files": [],
            "id": str(uuid.uuid4()),
            "project": str(project_id),
            "version": "1.0",
        }

        with open(qgis_tempdir.joinpath("deltafile.json"), "w") as f:
            json.dump(json_content, f)

        overwrite_conflicts_arg = "--overwrite-conflicts" if overwrite_conflicts else ""
        exit_code, output = run_docker(
            f"xvfb-run python3 entrypoint.py apply-delta {project_id} {project_file} {overwrite_conflicts_arg}",
            volumes={qgis_tempdir: {"bind": "/io/", "mode": "rw"}},
        )

        logger.info(
            f"""
===============================================================================
| Apply deltas finished
===============================================================================
Project ID: {project_id}
Project file: {project_file}
Exit code: {exit_code}
Output:
------------------------------------------------------------------------------S
{output.decode('utf-8')}
------------------------------------------------------------------------------E
"""
        )

        deltalog_file = os.path.join(orchestrator_tempdir, "deltalog.json")
        with open(deltalog_file, "r") as f:
            deltalog = json.load(f)

            for feedback in deltalog:
                delta_id = feedback["delta_id"]
                status = feedback["status"]

                if status == "status_applied":
                    status = Delta.Status.APPLIED
                elif status == "status_conflict":
                    status = Delta.Status.CONFLICT
                elif status == "status_apply_failed":
                    status = Delta.Status.NOT_APPLIED
                else:
                    status = Delta.Status.ERROR

                Delta.objects.filter(pk=delta_id).update(
                    last_status=status, last_feedback=feedback
                )

        job.status = Job.Status.FINISHED
        job.output = output.decode("utf-8")
        job.save()

        return exit_code, output.decode("utf-8")


def check_status():
    """Launch a container to check that everything is working
    correctly."""

    exit_code, output = run_docker('echo "QGIS container is running"')

    logger.info(
        "check_status, exit_code: {}, output:\n\n{}".format(
            exit_code, output.decode("utf-8")
        )
    )

    if not exit_code == 0:
        raise QgisException(output)
    return exit_code, output.decode("utf-8")