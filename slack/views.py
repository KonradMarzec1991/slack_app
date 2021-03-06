"""
Slack API viewsets
"""

import os
import json
from django.views.decorators.csrf import csrf_exempt
from django.http.response import HttpResponse
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
import requests

from tickets.models import Ticket

from .utils import (
    create_ticket,
    get_basic_ticket_attr,
    FrozenJSON,
    PROCESS,
    CANCELLED,
    ALREADY_REMOVED,
    GONE_WRONG
)

from .constants import Constants

from .actions import Actions


class SlackInformationViewSet(ViewSet):
    """
    Viewset returns general information about commands as Slack respond
    """

    # pylint: disable=no-self-use
    def create(self, request):
        # pylint disable=no-self-use
        channel_id = request.POST['channel_id']
        actions = Actions(channel_id)
        actions.send_message(blocks=json.dumps(actions.slack_information()))
        return Response(status=200)


class SlackTicketsListViewSet(ViewSet):
    """
    Viewset returns Slack respond as formatted text message with  user's tickets
    """

    # pylint: disable=no-self-use
    def create(self, request):
        feed = FrozenJSON(request.POST)
        user_tickets = Ticket.objects.filter(reporter=feed.user_name[0])
        actions = Actions(feed.channel_id)
        blocks = actions.show_tickets(user_tickets)

        data = {
            'token': actions.token,
            'channel': feed.channel_id,
            'blocks': json.dumps(blocks)
        }
        requests.post(os.getenv('URL_SEND_MESSAGE'), data=data)
        return Response(status=200)


class SlackDialogViewSet(ViewSet):
    """
    Viewset opens in Slack modal window
    """

    # pylint: disable=no-self-use
    def create(self, request):
        feed = FrozenJSON(request.POST)
        channel_id, trigger_id = feed.channel_id, feed.trigger_id

        a = Actions(channel_id)
        content = a.display_dialog(
            trigger_id=trigger_id,
            action_type='create_ticket'
        )
        if not content['ok']:
            return Response(GONE_WRONG)
        return Response(status=200)


@csrf_exempt
def slack_payload(request):  # pylint: disable=inconsistent-return-statements
    # pylint: disable=no-self-use
    """
    :param request: Slack request
    :return: depending on action of user function returns:
    A) New ticket, when user activated via `SlackDialogViewSet` modal window
       and submitted validated data in form
    B) Edited ticket, when user activated via `SlackTicketsListViewSet` message
       with list of tickets and clicked on one of *edit button*
    C) Deletes ticket, when user activated via `SlackTicketsListViewSet` message
       with list of tickets and clicked on one of *delete button*
    """
    data_dict = json.loads(request.POST['payload'])
    feed = FrozenJSON(data_dict)
    reporter, response_url = feed.user.name, feed.response_url
    channel_id, team_id = feed.channel.id, feed.team.id

    actions = Actions(channel_id)
    if feed.type == Constants.BLOCK:
        action_id = feed.actions[0].action_id
        type_action, ticket_id = action_id[0], int(action_id[1:])
        try:
            ticket = Ticket.objects.get(id=ticket_id)
        except Ticket.DoesNotExist:  # pylint: disable=no-member
            actions.send_message(text=ALREADY_REMOVED)
            return HttpResponse(status=200)

        if type_action == Constants.EDIT:
            actions.display_dialog(feed.trigger_id,
                                   Constants.EDIT_TICKET, ticket)
        elif type_action == Constants.DELETE:
            ticket.delete()
            actions.send_message(text=f'`Ticket {ticket_id}` removed.')
        return HttpResponse(status=200)

    if feed.type == Constants.DIALOG_CANCELLATION:
        # pylint: disable=no-else-return
        if feed.callback_id == Constants.EDIT_TICKET:

            ticket_id = feed.state
            actions.send_message(
                text=f'*Modification* of `ticket id:{ticket_id}` '
                     f'has been cancelled.'
            )
            return HttpResponse(status=200)
        elif feed.callback_id == Constants.CREATE_TICKET:
            actions.send_message(text=CANCELLED)
            return HttpResponse(status=200)

    if feed.type == Constants.DIALOG_SUBMISSION:
        # pylint: disable=no-else-return
        if feed.callback_id == Constants.CREATE_TICKET:
            create_ticket.delay(
                data_dict, reporter, channel_id, team_id, response_url
            )
            actions.send_message(text=PROCESS)
            return HttpResponse(status=200)
        elif feed.callback_id == Constants.EDIT_TICKET:
            ticket = Ticket.objects.get(id=int(feed.state))
            ticket.title, ticket.description, ticket.status, \
            ticket.severity = get_basic_ticket_attr(feed)

            ticket.save()
            actions.send_message(
                text=f'`Ticket id:{int(feed.state)}` has been modified.')
            return HttpResponse(status=200)
