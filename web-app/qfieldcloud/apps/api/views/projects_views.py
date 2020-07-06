import os
import shutil

from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from django.conf import settings

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from qfieldcloud.apps.model.models import (
    Project, ProjectCollaborator)
from qfieldcloud.apps.api.serializers import (
    ProjectSerializer)
from qfieldcloud.apps.api.permissions import (
    ListCreateProjectPermission, ProjectPermission,
    IsProjectOwnerOrOrganizationMember)

User = get_user_model()

include_public_param = openapi.Parameter(
    'include-public', openapi.IN_QUERY,
    description="Include public projects",
    type=openapi.TYPE_BOOLEAN)


@method_decorator(
    name='retrieve', decorator=swagger_auto_schema(
        operation_description="Get a project", operation_id="Get a project",))
@method_decorator(
    name='update', decorator=swagger_auto_schema(
        operation_description="Update a project",
        operation_id="Update a project",))
@method_decorator(
    name='partial_update', decorator=swagger_auto_schema(
        operation_description="Patch a project",
        operation_id="Patch a project",))
@method_decorator(
    name='destroy', decorator=swagger_auto_schema(
        operation_description="Delete a project",
        operation_id="Delete a project",))
@method_decorator(
    name='list', decorator=swagger_auto_schema(
        operation_description="""List projects owned by the authenticated
        user or that the authenticated user has explicit permission to access
        (i.e. she is a project collaborator)""",
        operation_id="List projects",
        manual_parameters=[include_public_param]))
@method_decorator(
    name='create', decorator=swagger_auto_schema(
        operation_description="""Create a new project owned by the specified
        user or organization""",
        operation_id="Create a project",))
class ProjectViewSet(viewsets.ModelViewSet):

    serializer_class = ProjectSerializer
    # TODO: permissions

    def get_queryset(self):
        queryset = Project.objects.filter(owner=self.request.user) | \
            Project.objects.filter(
                collaborators__in=ProjectCollaborator.objects.filter(
                    collaborator=self.request.user))

        include_public = self.request.query_params.get(
            'include-public', default=None)

        if include_public and include_public.lower() == 'true':
            queryset = Project.objects.filter(owner=self.request.user) | \
                Project.objects.filter(
                    collaborators__in=ProjectCollaborator.objects.filter(
                        collaborator=self.request.user)) | \
                Project.objects.filter(private=False)

        return queryset

    # def get_permissions(self):
    #     self.permission_classes = [IsAuthenticated]

    #     if self.action in ['create', 'update', 'partial_update', 'destroy']:
    #         self.permission_classes = [IsAuthenticated,
    #                                    IsProjectOwnerOrOrganizationMember]

    #     return super().get_permissions()