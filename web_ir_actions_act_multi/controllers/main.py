# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import jinja2
import json
import logging
import os
import sys




from odoo.api import call_kw, Environment
from odoo import http, tools
from odoo.http import content_disposition, dispatch_rpc, request, serialize_exception as _serialize_exception, Response
from odoo.exceptions import AccessError, UserError, AccessDenied
from odoo.models import check_method_name
from odoo.service import db, security

from odoo.addons.web.controllers.main import DataSet

_logger = logging.getLogger(__name__)

if hasattr(sys, 'frozen'):
    # When running on compiled windows binary, we don't have access to package loader.
    path = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'views'))
    loader = jinja2.FileSystemLoader(path)
else:
    loader = jinja2.PackageLoader('odoo.addons.web', "views")

env = jinja2.Environment(loader=loader, autoescape=True)
env.filters["json"] = json.dumps

CONTENT_MAXAGE = http.STATIC_CACHE_LONG  # menus, translations, static qweb

DBNAME_PATTERN = '^[a-zA-Z0-9][a-zA-Z0-9_.-]+$'

COMMENT_PATTERN = r'Modified by [\s\w\-.]+ from [\s\w\-.]+'


def clean_action(action, env):
    action_type = action.setdefault('type', 'ir.actions.act_window_close')
    if action_type == 'ir.actions.act_window':
        action = fix_view_modes(action)
    elif action_type == 'ir.actions.act_multi':
        return action
    # When returning an action, keep only relevant fields/properties
    readable_fields = env[action['type']]._get_readable_fields()
    action_type_fields = env[action['type']]._fields.keys()

    cleaned_action = {
        field: value
        for field, value in action.items()
        # keep allowed fields and custom properties fields
        if field in readable_fields or field not in action_type_fields
    }

    # Warn about custom properties fields, because use is discouraged
    action_name = action.get('name') or action
    custom_properties = action.keys() - readable_fields - action_type_fields
    if custom_properties:
        _logger.warning("Action %r contains custom properties %s. Passing them "
            "via the `params` or `context` properties is recommended instead",
            action_name, ', '.join(map(repr, custom_properties)))

    return cleaned_action

# I think generate_views,fix_view_modes should go into js ActionManager
def generate_views(action):
    """
    While the server generates a sequence called "views" computing dependencies
    between a bunch of stuff for views coming directly from the database
    (the ``ir.actions.act_window model``), it's also possible for e.g. buttons
    to return custom view dictionaries generated on the fly.

    In that case, there is no ``views`` key available on the action.

    Since the web client relies on ``action['views']``, generate it here from
    ``view_mode`` and ``view_id``.

    Currently handles two different cases:

    * no view_id, multiple view_mode
    * single view_id, single view_mode

    :param dict action: action descriptor dictionary to generate a views key for
    """
    view_id = action.get('view_id') or False
    if isinstance(view_id, (list, tuple)):
        view_id = view_id[0]

    # providing at least one view mode is a requirement, not an option
    view_modes = action['view_mode'].split(',')

    if len(view_modes) > 1:
        if view_id:
            raise ValueError('Non-db action dictionaries should provide '
                             'either multiple view modes or a single view '
                             'mode and an optional view id.\n\n Got view '
                             'modes %r and view id %r for action %r' % (
                view_modes, view_id, action))
        action['views'] = [(False, mode) for mode in view_modes]
        return
    action['views'] = [(view_id, view_modes[0])]

def fix_view_modes(action):
    """ For historical reasons, Odoo has weird dealings in relation to
    view_mode and the view_type attribute (on window actions):

    * one of the view modes is ``tree``, which stands for both list views
      and tree views
    * the choice is made by checking ``view_type``, which is either
      ``form`` for a list view or ``tree`` for an actual tree view

    This methods simply folds the view_type into view_mode by adding a
    new view mode ``list`` which is the result of the ``tree`` view_mode
    in conjunction with the ``form`` view_type.

    TODO: this should go into the doc, some kind of "peculiarities" section

    :param dict action: an action descriptor
    :returns: nothing, the action is modified in place
    """
    if not action.get('views'):
        generate_views(action)

    if action.pop('view_type', 'form') != 'form':
        return action

    if 'view_mode' in action:
        action['view_mode'] = ','.join(
            mode if mode != 'tree' else 'list'
            for mode in action['view_mode'].split(','))
    action['views'] = [
        [id, mode if mode != 'tree' else 'list']
        for id, mode in action['views']
    ]

    return action

class DataSet(DataSet):

    @http.route('/web/dataset/call_button', type='json', auth="user")
    def call_button(self, model, method, args, kwargs):
        action = self._call_kw(model, method, args, kwargs)
        if isinstance(action, dict) and action.get('type') != '':
            return clean_action(action, env=request.env)
        return False


