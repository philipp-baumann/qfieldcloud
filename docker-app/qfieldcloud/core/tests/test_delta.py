import io
import json
import logging
import time

import fiona
import rest_framework
from django.http.response import FileResponse, HttpResponse
from qfieldcloud.authentication.models import AuthToken
from qfieldcloud.core import utils
from qfieldcloud.core.models import (
    Job,
    Organization,
    OrganizationMember,
    Person,
    Project,
    ProjectCollaborator,
)
from rest_framework import status
from rest_framework.test import APITransactionTestCase

from .utils import get_filename, setup_subscription_plans, testdata_path

logging.disable(logging.CRITICAL)


class QfcTestCase(APITransactionTestCase):
    def setUp(self):
        setup_subscription_plans()

        # Create a user
        self.user1 = Person.objects.create_user(username="user1", password="abc123")
        self.user1.save()
        self.user2 = Person.objects.create_user(username="user2", password="abc123")
        self.user2.save()
        self.user3 = Person.objects.create_user(username="user3", password="abc123")
        self.user3.save()

        self.token1 = AuthToken.objects.get_or_create(user=self.user1)[0]
        self.token2 = AuthToken.objects.get_or_create(user=self.user2)[0]
        self.token3 = AuthToken.objects.get_or_create(user=self.user3)[0]

        self.org1 = Organization.objects.create(
            username="org1", organization_owner=self.user1
        )

        # Create a project
        self.project1 = Project.objects.create(
            name="project1",
            is_public=False,
            owner=self.org1,
        )
        self.project1.save()

        self.project2 = Project.objects.create(
            name="project2",
            is_public=False,
            owner=self.user2,
        )
        self.project1.save()

        OrganizationMember.objects.create(
            organization=self.org1,
            member=self.user2,
        )
        ProjectCollaborator.objects.create(
            project=self.project1,
            collaborator=self.user2,
            role=ProjectCollaborator.Roles.REPORTER,
        )
        OrganizationMember.objects.create(
            organization=self.org1,
            member=self.user3,
        )
        ProjectCollaborator.objects.create(
            project=self.project1,
            collaborator=self.user3,
            role=ProjectCollaborator.Roles.ADMIN,
        )

    def tearDown(self):
        while True:
            # make sure there are no active jobs in the queue
            if (
                Job.objects.all()
                .filter(
                    status__in=[
                        Job.Status.PENDING,
                        Job.Status.QUEUED,
                        Job.Status.STARTED,
                    ]
                )
                .count()
                == 0
            ):
                time.sleep(1)
                return

    def fail(self, msg: str, job: Job = None):
        if job:
            msg += f"\n\nOutput:\n================\n{job.output}\n================"

            if job.feedback:
                if "error_stack" in job.feedback:
                    msg += "\n\nError:\n================"
                    for single_error_stack in job.feedback["error_stack"]:
                        msg += "\n"
                        msg += single_error_stack

                    msg += f"  {job.feedback['error']}\n================"

                feedback = json.dumps(job.feedback, indent=2, sort_keys=True)
                msg += f"\n\nFeedback:\n================\n{feedback}\n================"
            else:
                msg += "\n\nFeedback: None"

        super().fail(msg)

    def assertHttpOk(self, response: HttpResponse):
        try:
            self.assertTrue(
                rest_framework.status.is_success(response.status_code), response.json()
            )
        except Exception:
            self.assertTrue(
                rest_framework.status.is_success(response.status_code), response.content
            )

    def upload_project_files(self, project) -> Project:
        # Verify the original geojson file
        with open(testdata_path("delta/points.geojson")) as f:
            points_geojson = json.load(f)
            features = sorted(points_geojson["features"], key=lambda k: k["id"])
            self.assertEqual(1, features[0]["properties"]["int"])

        for project_file in [
            "points.geojson",
            "polygons.geojson",
            "testdata.gpkg",
            "project.qgs",
            "nonspatial.csv",
        ]:
            file_path = testdata_path(f"delta/{project_file}")
            response = self.client.post(
                f"/api/v1/files/{project.id}/{project_file}/",
                {"file": open(file_path, "rb")},
                format="multipart",
            )
            self.assertTrue(
                status.is_success(response.status_code),
                f"Failed to upload file '{project_file}'",
            )

        # wait until the project file check are ready
        for i in range(30):
            updated_project = Project.objects.get(id=project.id)
            if updated_project.project_filename:
                return updated_project

            time.sleep(1)

        raise Exception("Projectfile never set on project")

    def test_push_apply_delta_file(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta2.json",
            token=self.token1.key,
            final_values=[
                [
                    "c8c421cd-e39c-40a0-97d8-a319c245ba14",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, layer="points") as layer:
            features = list(layer)
            self.assertEqual(666, features[0]["properties"]["int"])

    def test_push_apply_delta_file_empty_source_layer_id(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta_empty_source_layer_id.json",
            token=self.token1.key,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, layer="points") as layer:
            features = list(layer)
            self.assertEqual(666, features[0]["properties"]["int"])

    def test_push_apply_delta_file_with_null_char(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta_null.json",
            token=self.token1.key,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, layer="points") as layer:
            features = list(layer)
            self.assertEqual("", features[0]["properties"]["str"])

    def test_push_apply_delta_file_with_error(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="with_errors.json",
            token=self.token1.key,
            final_values=[
                [
                    "65b605b4-9832-4de0-9055-92e1dd94ebec",
                    "STATUS_NOT_APPLIED",
                    self.user1.username,
                ]
            ],
        )

    def test_push_apply_delta_file_invalid_json_schema(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        bucket = utils.get_s3_bucket()
        prefix = utils.safe_join(f"projects/{project.id}/deltas/")
        wrong_deltas_before = list(bucket.objects.filter(Prefix=prefix))
        delta_file = testdata_path("delta/deltas/not_schema_valid.json")

        self.assertFalse(self.upload_deltas(project, "not_schema_valid.json"))

        # check it is uploaded
        wrong_deltas = list(bucket.objects.filter(Prefix=prefix))

        # TODO : cleanup buckets before in setUp so tests are completely independent
        self.assertEqual(len(wrong_deltas), len(wrong_deltas_before) + 1)

        f = self.get_delta_file_with_project_id(self.project1, delta_file)
        self.assertEqual(wrong_deltas[-1].get()["Body"].read().decode(), f.read())

    def test_push_apply_delta_file_not_json(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        delta_file = testdata_path("file.txt")

        response = self.client.post(
            f"/api/v1/deltas/{project.id}/",
            {"file": open(delta_file, "r")},
            format="multipart",
        )

        self.assertFalse(rest_framework.status.is_success(response.status_code))

    def test_push_apply_delta_file_conflicts_overwrite_true(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta_conflict.json",
            token=self.token1.key,
            final_values=[
                [
                    "8d185b67-f05e-40c6-9c9a-6ceca8100c39",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

    def test_push_apply_delta_file_twice(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta.json",
            token=self.token1.key,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, layer="points") as layer:
            features = list(layer)
            self.assertEqual(666, features[0]["properties"]["int"])

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta.json",
            token=self.token1.key,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        self.assertTrue(
            self.upload_deltas(project, "singlelayer_singledelta_diff_content.json")
        )

    def test_push_list_deltas(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.assertTrue(
            self.upload_deltas(self.project1, "singlelayer_singledelta3.json")
        )
        self.assertTrue(
            self.upload_deltas(self.project1, "singlelayer_singledelta4.json")
        )

        self.upload_and_check_deltas(
            project=project,
            delta_filename=None,
            token=self.token1.key,
            final_values=[
                [
                    "802ae2ef-f360-440e-a816-8990d6a06667",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
                [
                    "e4546ec2-6e01-43a1-ab30-a52db9469afd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
            ],
        )

    def test_push_list_multidelta(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_multidelta.json",
            token=self.token1.key,
            final_values=[
                [
                    "736bf2c2-646a-41a2-8c55-28c26aecd68d",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
                [
                    "8adac0df-e1d3-473e-b150-f8c4a91b4781",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
                [
                    "c6c88e78-172c-4f77-b2fd-2ff41f5aa854",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
            ],
        )

    def test_list_all_deltas_and_list_deltas_by_deltafile(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        self.assertTrue(
            self.upload_deltas(self.project1, "singlelayer_singledelta5.json")
        )
        self.assertTrue(
            self.upload_deltas(self.project1, "singlelayer_singledelta6.json")
        )

        # check all the deltas
        self.upload_and_check_deltas(
            project=project,
            delta_filename=None,
            token=self.token1.key,
            final_values=[
                [
                    "ad98634e-509f-4dff-9000-de79b09c5359",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
                [
                    "df6a19eb-7d61-4c64-9e3b-29bce0a8dfab",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
            ],
        )

        # check all the deltas from a single file
        self.upload_and_check_deltas(
            project=project,
            delta_filename=None,
            token=self.token1.key,
            final_values=[
                [
                    "ad98634e-509f-4dff-9000-de79b09c5359",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
            ],
            deltafile_id="3aab7e58-ea27-4b7c-9bca-c772b6d94820",
        )

    def test_push_apply_delta_file_conflicts_overwrite_false(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        # Set the overwrite_conflicts flag to False
        project.overwrite_conflicts = False
        project.save()

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_singledelta_conflict2.json",
            token=self.token1.key,
            final_values=[
                [
                    "bd507a3d-aa7b-42c4-bdb7-23ff34f65d5c",
                    "STATUS_CONFLICT",
                    self.user1.username,
                ]
            ],
        )

    def test_list_deltas_unexisting_project(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        self.upload_project_files(self.project1)

        response = self.client.get(
            "/api/v1/deltas/7199612e-7641-48fc-8c11-c25176a9761b/"
        )
        self.assertFalse(status.is_success(response.status_code))
        json = response.json()
        self.assertEqual(json["code"], "object_not_found")

    def test_push_delta_not_allowed(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token2.key)
        project = self.upload_project_files(self.project1)

        self.upload_and_check_deltas(
            project=project,
            delta_filename="singlelayer_multidelta_patch_create.json",
            token=self.token2.key,
            final_values=[
                [
                    "736bf2c2-646a-41a2-8c55-28c26aecd68d",
                    "STATUS_UNPERMITTED",
                    self.user2.username,
                ],
                [
                    "8adac0df-e1d3-473e-b150-f8c4a91b4781",
                    "STATUS_APPLIED",
                    self.user2.username,
                ],
            ],
        )

    def test_non_spatial_delta(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        # Push a deltafile
        self.upload_and_check_deltas(
            project=project,
            delta_filename="nonspatial.json",
            token=self.token1.key,
            final_values=[
                [
                    "1270b97d-6a28-49cc-83f3-b827ec574fee",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
                [
                    "f326c3c1-138f-4261-9151-4946237ce714",
                    "STATUS_APPLIED",
                    self.user1.username,
                ],
            ],
        )

        self.assertEqual(
            self.get_file_contents(project, "nonspatial.csv"), b'fid,col1\n"1",qux\n'
        )

    def test_delta_pushed_after_job_triggered(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        # Push a deltafile
        deltafile1_name = "singlelayer_singledelta.json"
        self.assertTrue(self.upload_deltas(project, deltafile1_name))
        with open(testdata_path(f"delta/deltas/{deltafile1_name}")) as f:
            deltafile1_id = json.load(f)["id"]

        deltafile2_name = "singlelayer_singledelta2.json"
        self.assertTrue(self.upload_deltas(project, deltafile2_name))
        with open(testdata_path(f"delta/deltas/{deltafile2_name}")) as f:
            deltafile2_id = json.load(f)["id"]

        self.check_deltas_by_file_id(
            project,
            deltafile1_id,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
            token=self.token1.key,
        )

        self.check_deltas_by_file_id(
            project,
            deltafile2_id,
            final_values=[
                [
                    "c8c421cd-e39c-40a0-97d8-a319c245ba14",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
            token=self.token1.key,
        )

    def test_delta_pushed_after_job_triggered_two_projects(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project1 = self.upload_project_files(self.project1)

        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token2.key)
        project2 = self.upload_project_files(self.project2)

        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)

        # Push a deltafile
        deltafile1_name = "singlelayer_singledelta.json"
        self.assertTrue(self.upload_deltas(project1, deltafile1_name))
        with open(testdata_path(f"delta/deltas/{deltafile1_name}")) as f:
            deltafile1_id = json.load(f)["id"]

        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token2.key)
        deltafile2_name = "singlelayer_singledelta_project2.json"
        self.assertTrue(self.upload_deltas(project2, deltafile2_name))
        with open(testdata_path(f"delta/deltas/{deltafile2_name}")) as f:
            deltafile2_id = json.load(f)["id"]

        self.check_deltas_by_file_id(
            project1,
            deltafile1_id,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
            token=self.token1.key,
        )

        self.check_deltas_by_file_id(
            project2,
            deltafile2_id,
            final_values=[
                [
                    "f2af4942-e4ab-446e-bd97-5aab17e7ccc1",
                    "STATUS_APPLIED",
                    self.user2.username,
                ]
            ],
            token=self.token2.key,
        )

    def test_change_and_delete_pushed_only_features(self):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + self.token1.key)
        project = self.upload_project_files(self.project1)

        # 1) client 1 creates a feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p1_c1_create.json",
            token=self.token1.key,
            final_values=[
                [
                    "9311eb96-bff8-4d5b-ab36-c314a007cfcd",
                    "STATUS_APPLIED",
                    self.user1.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 4)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)
            self.assertEqual(features[3]["properties"]["int"], 1000)

        # 2) client 2 creates a feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p2_c2_create.json",
            token=self.token3.key,
            final_values=[
                [
                    "608bbfb7-fb9c-49c4-818f-f636ee4ec20a",
                    "STATUS_APPLIED",
                    self.user3.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 5)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)
            self.assertEqual(features[3]["properties"]["int"], 1000)
            self.assertEqual(features[4]["properties"]["int"], 2000)

        # 3) client 1 updates their created feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p3_c1_patch.json",
            token=self.token3.key,
            final_values=[
                [
                    "f11603e5-13b2-43a9-b27a-db722297773b",
                    "STATUS_APPLIED",
                    self.user3.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 5)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)
            self.assertEqual(features[3]["properties"]["int"], 1001)
            self.assertEqual(features[4]["properties"]["int"], 2000)

        # 4) client 2 updates their created feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p4_c2_patch.json",
            token=self.token3.key,
            final_values=[
                [
                    "582de6de-562f-4482-9350-5b5aaa25d822",
                    "STATUS_APPLIED",
                    self.user3.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 5)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)
            self.assertEqual(features[3]["properties"]["int"], 1001)
            self.assertEqual(features[4]["properties"]["int"], 2002)

        # 5) client 1 deletes their created feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p5_c1_delete.json",
            token=self.token3.key,
            final_values=[
                [
                    "b7a09a1d-9626-4da0-8456-61c2ff884611",
                    "STATUS_APPLIED",
                    self.user3.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 4)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)
            self.assertEqual(features[3]["properties"]["int"], 2002)

        # 6) client 2 deletes their created feature
        self.upload_and_check_deltas(
            project=project,
            delta_filename="multistage_p6_c2_delete.json",
            token=self.token3.key,
            final_values=[
                [
                    "7cb988e9-5de2-4bd7-af4b-c1d27a2d579f",
                    "STATUS_APPLIED",
                    self.user3.username,
                ]
            ],
        )

        gpkg = io.BytesIO(self.get_file_contents(project, "testdata.gpkg"))
        with fiona.open(gpkg, "r", layer="points") as layer:
            features = list(layer)

            self.assertEqual(len(features), 3)
            self.assertEqual(features[0]["properties"]["int"], 1)
            self.assertEqual(features[1]["properties"]["int"], 2)
            self.assertEqual(features[2]["properties"]["int"], 3)

    def get_file_contents(self, project, filename):
        response = self.client.get(f"/api/v1/files/{project.id}/{filename}/")

        self.assertTrue(status.is_success(response.status_code))
        self.assertEqual(get_filename(response), filename)

        if isinstance(response, FileResponse):
            return b"".join(response.streaming_content)
        else:
            return response.content

    def get_delta_file_with_project_id(self, project, delta_filename):
        """Retrieves a delta json file with the project id replaced by the project.id"""
        with open(delta_filename, "r") as f:
            deltafile = json.load(f)
            deltafile["project"] = str(project.id)
            json_str = json.dumps(deltafile)
            return io.StringIO(json_str)

    def upload_deltas(self, project, delta_filename):
        delta_file = testdata_path(f"delta/deltas/{delta_filename}")

        response = self.client.post(
            f"/api/v1/deltas/{project.id}/",
            {"file": self.get_delta_file_with_project_id(project, delta_file)},
            format="multipart",
        )
        return rest_framework.status.is_success(response.status_code)

    def upload_and_check_deltas(
        self,
        project,
        delta_filename,
        final_values,
        token,
        wait_status=["STATUS_PENDING", "STATUS_BUSY"],
        failing_status=["STATUS_ERROR"],
        immediate_values=None,
        deltafile_id=None,
    ):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token)

        if delta_filename is not None:
            # Push a deltafile
            self.assertTrue(self.upload_deltas(project, delta_filename))

            delta_file = testdata_path(f"delta/deltas/{delta_filename}")

            if not deltafile_id:
                with open(delta_file) as f:
                    deltafile_id = json.load(f)["id"]

        self.check_deltas_by_file_id(
            project,
            deltafile_id,
            final_values,
            token,
            wait_status,
            failing_status,
            immediate_values,
        )

    def check_deltas_by_file_id(
        self,
        project,
        deltafile_id,
        final_values,
        token,
        wait_status=["STATUS_PENDING", "STATUS_BUSY"],
        failing_status=["STATUS_ERROR"],
        immediate_values=None,
    ):
        self.client.credentials(HTTP_AUTHORIZATION="Token " + token)

        uri = f"/api/v1/deltas/{project.id}/"
        if deltafile_id:
            uri = f"{uri}{deltafile_id}/"

        response = self.client.get(uri)
        self.assertTrue(rest_framework.status.is_success(response.status_code))
        payload = response.json()
        payload = sorted(payload, key=lambda k: k["id"])

        if immediate_values:
            self.assertEqual(len(payload), len(immediate_values))

            for idx, immediate_value in enumerate(immediate_values):
                delta_id, status, created_by = immediate_value
                status = status if isinstance(status, list) else list(status)

                self.assertEqual(payload[idx]["id"], delta_id)
                self.assertIn(payload[idx]["status"], status)
                self.assertEqual(payload[idx]["created_by"], created_by)

        job = Job.objects.filter(
            project=self.project1,
            type=Job.Type.DELTA_APPLY,
        ).latest("updated_at")

        for _ in range(10):

            time.sleep(2)
            response = self.client.get(uri)

            self.assertHttpOk(response)

            payload = response.json()
            payload = sorted(payload, key=lambda k: k["id"])

            self.assertEqual(len(payload), len(final_values))

            for idx, final_value in enumerate(final_values):
                if payload[idx]["status"] in wait_status:
                    break

                if payload[idx]["status"] in failing_status:
                    job.refresh_from_db()
                    self.fail(f"Got failing status {payload[idx]['status']}", job=job)
                    return

                delta_id, status, created_by = final_value
                status = status if isinstance(status, list) else [status]

                self.assertEqual(payload[idx]["id"], delta_id)
                self.assertIn(payload[idx]["status"], status)
                self.assertEqual(payload[idx]["created_by"], created_by)

                if len(final_values) == idx + 1:
                    return

        self.fail("Worker didn't finish", job=job)
