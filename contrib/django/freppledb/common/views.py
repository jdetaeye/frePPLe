#
# Copyright (C) 2007-2012 by Johan De Taeye, frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

# file : $URL$
# revision : $LastChangedRevision$  $LastChangedBy$
# date : $LastChangedDate$

from datetime import datetime

from django.shortcuts import render_to_response
from django.views.decorators.csrf import csrf_protect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.contenttypes.models import ContentType
from django.db import DEFAULT_DB_ALIAS
from django.template import RequestContext
from django import forms
from django.forms.models import modelformset_factory
from django.utils.encoding import force_unicode
from django.utils.translation import ugettext_lazy as _
from django.utils.text import capfirst
from django.contrib.auth.models import User, Group
from django.contrib.admin.models import LogEntry
from django.contrib.syndication.views import Feed
from django.utils import translation
from django.conf import settings
from django.http import Http404, HttpResponseRedirect


from freppledb.common.models import Preferences, Parameter, Comment, Bucket, BucketDetail
from freppledb.common.report import GridReport, GridFieldLastModified, GridFieldText
from freppledb.common.report import GridFieldBool, GridFieldDateTime, GridFieldInteger


import logging
logger = logging.getLogger(__name__)


class PreferencesForm(forms.Form):
  language = forms.ChoiceField(label = _("language"),
    initial="auto",
    choices=Preferences.languageList,
    help_text=_("Language of the user interface"),
    )
  buckets = forms.ModelChoiceField(queryset=Bucket.objects.all().values_list('name', flat=True),
    label=_("Buckets"),
    required=False,
    help_text=_("Time bucket size for reports"),
    )
  startdate = forms.DateField(label = _("report start date"),
    required=False,
    help_text=_("Start date for filtering report data"),
    widget=forms.TextInput(attrs={'class':"vDateField"}),
    )
  enddate = forms.DateField(label = _("report end date"),
    required=False,
    help_text=_("End date for filtering report data"),
    widget=forms.TextInput(attrs={'class':"vDateField"}),
    )
  pagesize = forms.IntegerField(label = _('page size'),
    required=False,
    initial=100,
    min_value=25,
    help_text = _('Number of records to fetch in a single page from the server'),                            
    )
  theme = forms.ChoiceField(label = _('theme'),
    required=False,
    choices=settings.THEMES,
    help_text=_('Theme for the user interface'),
    )

@login_required
@csrf_protect
def preferences(request):
  if request.method == 'POST':
    form = PreferencesForm(request.POST)
    if form.is_valid():
      try:
        pref = Preferences.objects.get(user=request.user)
        newdata = form.cleaned_data
        pref.buckets = newdata['buckets']
        if newdata['startdate']:
          pref.startdate = datetime(newdata['startdate'].year, newdata['startdate'].month, newdata['startdate'].day)
        if newdata['enddate']:
          pref.enddate = datetime(newdata['enddate'].year, newdata['enddate'].month, newdata['enddate'].day)
        pref.language = newdata['language']
        pref.theme = newdata['theme']
        pref.pagesize = newdata['pagesize']
        pref.save()
        # Switch to the new theme and language immediately
        request.theme = newdata['theme']
        if translation.get_language() != newdata['language']:
          translation.activate(newdata['language'])
          request.LANGUAGE_CODE = translation.get_language()
        messages.add_message(request, messages.INFO, force_unicode(_('Successfully updated preferences')))
      except Exception as e:
        logger.error("Failure updating preferences: %s" % e)
        messages.add_message(request, messages.ERROR, force_unicode(_('Failure updating preferences')))
  else:
    pref = request.user.get_profile()
    form = PreferencesForm({
      'buckets': pref.buckets,
      'startdate': pref.startdate and pref.startdate.date() or None,
      'enddate': pref.enddate and pref.enddate.date() or None,
      'language': pref.language,
      'theme': pref.theme,
      'pagesize': pref.pagesize,
      })
  return render_to_response('common/preferences.html', {
     'title': _('Edit my preferences'),
     'form': form,
     },
     context_instance=RequestContext(request))


class UserList(GridReport):
  '''
  A list report to show users.
  '''
  template = 'auth/userlist.html'
  title = _("User List")
  basequeryset = User.objects.all()
  model = User
  frozenColumns = 1
  multiselect = False
  
  rows = (
    GridFieldInteger('id', title=_('identifier'), key=True, formatter='user'),          
    GridFieldText('username', title=_('username')),          
    GridFieldText('email', title=_('E-mail'), formatter='email', width=200),          
    GridFieldText('first_name', title=_('first_name')),          
    GridFieldText('last_name', title=_('last name')),          
    GridFieldBool('is_staff', title=_('staff status')),          
    )


class GroupList(GridReport):
  '''
  A list report to show groups.
  '''
  template = 'auth/grouplist.html'
  title = _("Group List")
  basequeryset = Group.objects.all()
  model = Group
  frozenColumns = 0
  multiselect = False
  rows = (
    GridFieldInteger('id', title=_('identifier'), key=True, formatter='group'),          
    GridFieldText('name', title=_('name'), key=True, width=200),          
    )


class ParameterList(GridReport):
  '''
  A list report to show all configurable parameters.
  '''
  template = 'common/parameterlist.html'
  title = _("Parameter List")
  basequeryset = Parameter.objects.all()
  model = Parameter
  frozenColumns = 1

  rows = (
    GridFieldText('name', title=_('name'), key=True, formatter='parameter'),
    GridFieldText('value', title=_('value')),
    GridFieldText('description', title=_('description')),
    GridFieldLastModified('lastmodified'),
    )


@staff_member_required
@csrf_protect
def Comments(request, app, model, object_id):
  try:
    modeltype = ContentType.objects.using(request.database).get(app_label=app, model=model)
    modeltype._state.db = request.database
    modelinstance = modeltype.get_object_for_this_type(pk=object_id) 
    comments = Comment.objects.using(request.database). \
      filter(content_type__pk = modeltype.id, object_pk = object_id). \
      order_by('-id')
  except:
    raise Http404('Object not found')  
  if request.method == 'POST':    
    comment = request.POST['comment']
    if comment:
      request.user._state.db = request.database  # Need to lie a bit
      Comment(
           content_object = modelinstance,
           user = request.user,
           comment = comment
           ).save(using=request.database)
    return HttpResponseRedirect('%s/comments/%s/%s/%s/' % (request.prefix,app, model, object_id))
  else:       
    return render_to_response('common/comments.html', { 
      'title': capfirst(force_unicode(modelinstance._meta.verbose_name) + " " + object_id),
      'model': model,
      'object_id': object_id,
      'active_tab': 'comments',
      'comments': comments
      },
      context_instance=RequestContext(request))   
   

class CommentList(GridReport):
  '''
  A list report to review the history of actions.
  '''
  template = 'common/commentlist.html'
  title = _('Comments')
  basequeryset = Comment.objects.all()
  default_sort = (0,'desc')
  model = Comment
  editable = False
  multiselect = False
  frozenColumns = 0
  
  rows = (
    GridFieldInteger('id', title=_('identifier'), key=True),
    GridFieldLastModified('lastmodified'),
    GridFieldText('user', title=_('user'), field_name='user__username', editable=False, align='center', width=80),
    GridFieldText('type', title=_('type'), field_name='content_type__name', editable=False, align='center'),
    GridFieldText('object', title=_('object ID'), field_name='object_pk', editable=False, align='center', extra='formatter:objectfmt'),
    GridFieldText('comment', title=_('comment'), editable=False, align='center'),
    )  


class BucketList(GridReport):
  '''
  A list report to show dates.
  '''
  template = 'common/bucketlist.html'
  title = _("Bucket List")
  basequeryset = Bucket.objects.all()
  model = Bucket
  frozenColumns = 1
  rows = (
    GridFieldText('name', title=_('name'), key=True, formatter="bucket"),
    GridFieldText('description', title=_('description')),
    GridFieldLastModified('lastmodified'),
    )


class BucketDetailList(GridReport):
  '''
  A list report to show dates.
  '''
  template = 'common/bucketlist.html'
  title = _("Bucket Detail List")
  basequeryset = BucketDetail.objects.all()
  model = BucketDetail
  frozenColumns = 2
  rows = (
    GridFieldText('bucket', title=_('bucket'), field_name='bucket__name', formatter="bucket"),
    GridFieldDateTime('startdate', title=_('start date')),
    GridFieldDateTime('enddate', title=_('end date')),
    GridFieldText('name', title=_('name')),
    GridFieldLastModified('lastmodified'),
    )
