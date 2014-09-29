# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Q
from django.utils import timezone

import bleach
import jinja2
from markdown import markdown
from tower import ugettext as _

from oneanddone.base.models import CachedModel, CreatedByModel, CreatedModifiedModel


class TaskInvalidationCriterion(CreatedModifiedModel, CreatedByModel):
    """
    Condition that should cause a Task to become invalid.
    """

    class Meta(CreatedModifiedModel.Meta):
        verbose_name_plural = "task invalidation criteria"

    EQUAL = 0
    NOT_EQUAL = 1
    choices = {EQUAL: '==', NOT_EQUAL: '!='}

    field_name = models.CharField(max_length=80)
    relation = models.IntegerField(choices=choices.items(),
                                   default=EQUAL)
    field_value = models.CharField(max_length=80)
    batches = models.ManyToManyField('TaskImportBatch')

    def __unicode__(self):
        return ' '.join([str(self.field_name),
                         self.choices[self.relation],
                         self.field_value])

    field_name.help_text = """
        Name of field recognized by Bugzilla@Mozilla REST API. Examples:
        status, resolution, component.
    """
    field_value.help_text = """
        Target value of the field to be checked.
    """
    relation.help_text = """
        Relationship (equality/inequality) between name and value.
    """


class TaskImportBatch(CreatedModifiedModel, CreatedByModel):
    """
        Set of Tasks created in one step based on an external search query.
        One Task is created per query result.
    """
    description = models.CharField(max_length=255,
                                   verbose_name='batch summary')
    query = models.TextField(verbose_name='query URL')
    # other sources might be Moztrap, etc.
    BUGZILLA = 0
    OTHER = 1
    source = models.IntegerField(
        choices=(
            (BUGZILLA, 'Bugzilla@Mozilla'),
            (OTHER, 'Other')
        ),
        default=BUGZILLA)

    def __unicode__(self):
        return self.description

    query.help_text = """
        The URL to the Bugzilla@Mozilla search query that yields the items you
        want to create tasks from.
    """
    description.help_text = """
        A summary of what items are being imported.
    """


class BugzillaBug(models.Model):
    summary = models.CharField(max_length=255)
    bugzilla_id = models.IntegerField(max_length=20, unique=True)
    tasks = generic.GenericRelation('Task')

    def __unicode__(self):
        return ' '.join(['Bug', str(self.bugzilla_id)])


class TaskProject(CachedModel, CreatedModifiedModel, CreatedByModel):
    name = models.CharField(max_length=255)

    def __unicode__(self):
        return self.name


class TaskTeam(CachedModel, CreatedModifiedModel, CreatedByModel):
    name = models.CharField(max_length=255)

    def __unicode__(self):
        return self.name


class TaskType(CachedModel, CreatedModifiedModel, CreatedByModel):
    name = models.CharField(max_length=255)

    def __unicode__(self):
        return self.name


class Task(CachedModel, CreatedModifiedModel, CreatedByModel):
    """
    Task for a user to attempt to fulfill.
    """

    class Meta(CreatedModifiedModel.Meta):
        ordering = ['priority', 'difficulty']

    project = models.ForeignKey(TaskProject, blank=True, null=True)
    team = models.ForeignKey(TaskTeam)
    type = models.ForeignKey(TaskType, blank=True, null=True)

    # imported_item may be BugzillaBug for now. In future, other sources such
    # as Moztrap may be possible
    content_type = models.ForeignKey(ContentType, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    imported_item = generic.GenericForeignKey('content_type', 'object_id')

    # batch that created this Task
    batch = models.ForeignKey(TaskImportBatch, blank=True, null=True)

    BEGINNER = 1
    INTERMEDIATE = 2
    ADVANCED = 3
    difficulty = models.IntegerField(
        choices=(
            (BEGINNER, 'Beginner'),
            (INTERMEDIATE, 'Intermediate'),
            (ADVANCED, 'Advanced')
        ),
        default=BEGINNER,
        verbose_name='task difficulty')

    P1 = 1
    P2 = 2
    P3 = 3
    priority = models.IntegerField(
        choices=(
            (P1, 'P1'),
            (P2, 'P2'),
            (P3, 'P3')
        ),
        default=P3,
        verbose_name='task priority')
    end_date = models.DateTimeField(blank=True, null=True)
    execution_time = models.IntegerField(
        choices=((i, i) for i in (15, 30, 45, 60)),
        blank=False,
        default=30,
        verbose_name='estimated time'
    )
    instructions = models.TextField()
    is_draft = models.BooleanField(verbose_name='draft')
    is_invalid = models.BooleanField(verbose_name='invalid')
    name = models.CharField(max_length=255, verbose_name='title')
    prerequisites = models.TextField(blank=True)
    repeatable = models.BooleanField(default=True)
    short_description = models.CharField(max_length=255, verbose_name='description')
    start_date = models.DateTimeField(blank=True, null=True)
    why_this_matters = models.TextField(blank=True)

    def save(self, *args, **kwargs):
        super(Task, self).save(*args, **kwargs)
        if not self.is_available:
            # Close any open attempts
            self.taskattempt_set.filter(state=TaskAttempt.STARTED).update(
                state=TaskAttempt.CLOSED,
                requires_notification=True)

    def _yield_html(self, field):
        """
        Return the requested field for a task after parsing them as
        markdown and bleaching/linkifying them.
        """
        linkified_field = bleach.linkify(field, parse_email=True)
        html = markdown(linkified_field, output_format='html5')
        cleaned_html = bleach.clean(html, tags=settings.INSTRUCTIONS_ALLOWED_TAGS,
                                    attributes=settings.INSTRUCTIONS_ALLOWED_ATTRIBUTES)
        return jinja2.Markup(cleaned_html)

    @property
    def keywords_list(self):
        return ', '.join([keyword.name for keyword in self.keyword_set.all()])

    def replace_keywords(self, keywords, creator):
        self.keyword_set.all().delete()
        for keyword in keywords:
            if len(keyword):
                self.keyword_set.create(name=keyword, creator=creator)

    @property
    def is_available(self):
        """Whether this task is available for users to attempt."""
        if self.is_draft or self.is_invalid:
            return False

        now = timezone.now()
        return not (
            (self.end_date and now > self.end_date) or
            (self.start_date and now < self.start_date)
        )

    def is_available_to_user(self, user):
        repeatable_filter = Q(~Q(user=user) & ~Q(state=TaskAttempt.ABANDONED))
        return self.is_available and (
            self.repeatable or not self.taskattempt_set.filter(repeatable_filter).exists())

    @property
    def is_taken(self):
        return (not self.repeatable and
                self.taskattempt_set.filter(
                    state=TaskAttempt.STARTED).exists())

    @property
    def is_completed(self):
        return (not self.repeatable and
                self.taskattempt_set.filter(
                    state=TaskAttempt.FINISHED).exists())

    @property
    def instructions_html(self):
        return self._yield_html(self.instructions)

    @property
    def prerequisites_html(self):
        return self._yield_html(self.prerequisites)

    @property
    def why_this_matters_html(self):
        return self._yield_html(self.why_this_matters)

    def get_absolute_url(self):
        return reverse('tasks.detail', args=[self.id])

    def get_edit_url(self):
        return reverse('tasks.edit', args=[self.id])

    def __unicode__(self):
        return self.name

    @classmethod
    def is_available_filter(self, now=None, allow_expired=False, prefix=''):
        """
        Return a Q object (queryset filter) that matches available
        tasks.

        :param now:
            Datetime to use as the current datetime. Defaults to
            django.utils.timezone.now().

        :param allow_expired:
            If False, exclude tasks past their end date.

        :param prefix:
            Prefix to use for queryset filter names. Good for when you
            want to filter on a related tasks and need 'task__'
            prepended to the filters.
        """
        # Convenient shorthand for creating a Q filter with the prefix.
        pQ = lambda **kwargs: Q(**dict((prefix + key, value) for key, value in kwargs.items()))

        now = now or timezone.now()
        # Use just the date to allow caching
        now = now.replace(hour=0, minute=0, second=0)
        q_filter = (pQ(is_draft=False) & pQ(is_invalid=False) &
                    (pQ(start_date__isnull=True) | pQ(start_date__lte=now)))

        if not allow_expired:
            q_filter = q_filter & (pQ(end_date__isnull=True) | pQ(end_date__gt=now))

        q_filter = q_filter & (
            pQ(repeatable=True) | (
                ~pQ(taskattempt_set__state=TaskAttempt.STARTED) &
                ~pQ(taskattempt_set__state=TaskAttempt.FINISHED)))

        return q_filter

    # Help text
    instructions.help_text = """
        Markdown formatting is applied. See
        <a href="http://www.markdowntutorial.com/">http://www.markdowntutorial.com/</a> for a
        primer on Markdown syntax.
    """
    execution_time.help_text = """
        How many minutes will this take to finish?
    """
    start_date.help_text = """
        Date the task will start to be available. Task is immediately available if blank.
    """
    end_date.help_text = """
        If a task expires, it will not be shown to users regardless of whether it has been
        finished.
    """
    is_draft.help_text = """
        If you do not wish to publish the task yet, set it as a draft. Draft tasks will not
        be viewable by contributors.
    """


class TaskKeyword(CachedModel, CreatedModifiedModel, CreatedByModel):
    task = models.ForeignKey(Task, related_name='keyword_set')

    name = models.CharField(max_length=255, verbose_name='keyword')

    def __unicode__(self):
        return self.name


class TaskAttempt(CachedModel, CreatedModifiedModel):
    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL)
    task = models.ForeignKey(Task, related_name='taskattempt_set')

    STARTED = 0
    FINISHED = 1
    ABANDONED = 2
    CLOSED = 3
    state = models.IntegerField(default=STARTED, choices=(
        (STARTED, 'Started'),
        (FINISHED, 'Finished'),
        (ABANDONED, 'Abandoned'),
        (CLOSED, 'Closed')
    ))
    requires_notification = models.BooleanField(default=False)

    def __unicode__(self):
        return u'{user} attempt [{task}]'.format(user=self.user, task=self.task)

    @property
    def feedback_display(self):
        if self.has_feedback:
            return self.feedback.text
        return _('No feedback for this attempt')

    @property
    def has_feedback(self):
        try:
            self.feedback
            return True
        except Feedback.DoesNotExist:
            return False

    @property
    def attempt_length_in_minutes(self):
        start_seconds = time.mktime(self.created.timetuple())
        end_seconds = time.mktime(self.modified.timetuple())
        return round((end_seconds - start_seconds) / 60, 1)

    class Meta(CreatedModifiedModel.Meta):
        ordering = ['-modified']

    @classmethod
    def close_stale_onetime_attempts(self):
        """
        Close any attempts for one-time tasks that have been open for over 30 days
        """
        compare_date = timezone.now() - timedelta(days=settings.TASK_ATTEMPT_EXPIRATION_DURATION)
        expired_onetime_attempts = self.objects.filter(
            state=self.STARTED,
            created__lte=compare_date,
            task__repeatable=False)
        return expired_onetime_attempts.update(
            state=self.CLOSED,
            requires_notification=True)

    @classmethod
    def close_expired_task_attempts(self):
        """
        Close any attempts for tasks that have expired
        """
        open_attempts = self.objects.filter(state=self.STARTED)
        closed = 0
        for attempt in open_attempts:
            if not attempt.task.is_available:
                attempt.state = self.CLOSED
                attempt.requires_notification = True
                attempt.save()
                closed += 1
        return closed


class Feedback(CachedModel, CreatedModifiedModel):
    attempt = models.OneToOneField(TaskAttempt)
    text = models.TextField()

    def __unicode__(self):
        return u'Feedback: {user} for {task}'.format(
            user=self.attempt.user, task=self.attempt.task)
