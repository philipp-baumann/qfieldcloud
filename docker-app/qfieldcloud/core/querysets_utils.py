from functools import reduce
from operator import and_, or_

from django.db.models import Q
from django.db.models import Value as V
from django.db.models.functions import StrIndex
from django.db.models.manager import BaseManager
from qfieldcloud.core.models import (
    Delta,
    Organization,
    OrganizationMember,
    Project,
    ProjectCollaborator,
    Team,
    TeamMember,
    User,
)


def get_organization_teams(organization):
    return Team.objects.filter(team_organization=organization)


def get_team_members(team):
    return TeamMember.objects.filter(team=team)


def get_organization_members(organization):
    return OrganizationMember.objects.filter(organization=organization)


def get_project_deltas(project):
    """Return a queryset of all available deltas of a specific project."""
    return Delta.objects.filter(project=project)


def get_users(
    username: str,
    project: Project = None,
    organization: Organization = None,
    exclude_organizations: bool = False,
    exclude_teams: bool = False,
    invert: bool = False,
) -> BaseManager:
    assert (
        project is None or organization is None
    ), "Cannot have the project and organization filters set simultaneously"

    if username:
        users = User.objects.filter(username__icontains=username)
    else:
        users = User.objects.all()

    if exclude_organizations:
        users = users.exclude(type=User.Type.ORGANIZATION)

    if exclude_teams:
        users = users.exclude(type=User.Type.TEAM)
    else:
        if project:
            users = users.filter(
                ~Q(type=User.Type.TEAM)
                | (
                    Q(type=User.Type.TEAM)
                    & Q(
                        pk__in=Team.objects.filter(
                            team_organization_id=project.owner_id
                        )
                    )
                )
            )

    # one day conditions can be more than just pk check, please keep it for now
    conditions = []
    # exclude the already existing collaborators and the project owner
    if project:
        collaborator_ids = ProjectCollaborator.objects.filter(
            project=project
        ).values_list("collaborator", flat=True)
        user_ids = [*collaborator_ids, project.owner.pk]
        conditions = [Q(pk__in=user_ids)]

    # exclude the already existing members, the organization owner and the organization itself from the returned users
    elif organization:
        # exclude all teams that are not of the current organization
        users = users.filter(
            ~Q(type=User.Type.TEAM)
            | Q(pk__in=Team.objects.filter(team_organization=organization))
        )

        member_ids = OrganizationMember.objects.filter(
            organization=organization
        ).values_list("member", flat=True)
        user_ids = [
            *member_ids,
            organization.organization_owner.pk,
            organization.user_ptr.pk,
        ]
        conditions = [Q(pk__in=user_ids)]

    if conditions:
        if invert:
            users = users.filter(reduce(and_, [c for c in conditions]))
        else:
            users = users.exclude(reduce(or_, [c for c in conditions]))

    return users.annotate(
        ordering=StrIndex("username", V(username)),
    ).order_by("ordering", "username")
