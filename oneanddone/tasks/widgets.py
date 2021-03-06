# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
from django.forms import CheckboxSelectMultiple
from django.forms.widgets import DateInput, Input, RadioSelect
from django.utils.safestring import mark_safe


class RangeInput(Input):
    input_type = 'range'

    def render(self, name, value, attrs=None):
        markup = """
            <div class="range-container">
              <div class="range-row">
                {input} &nbsp; <span class="range-label"></span>
              </div>
              <div class="steps">
                <span style="left: 0%">0</span>
                <span style="left: 25%">15</span>
                <span style="left: 50%">30</span>
                <span style="left: 75%">45</span>
                <span style="left: 100%">60</span>
              </div>
            </div>
        """.format(input=super(RangeInput, self).render(name, value, attrs))

        return mark_safe(markup)


class CalendarInput(DateInput):

    def render(self, name, value, attrs={}):
        if 'class' not in attrs:
            attrs['class'] = 'datepicker'
        return super(CalendarInput, self).render(name, value, attrs)


class HorizRadioRenderer(RadioSelect.renderer):
    """ this overrides widget method to put radio buttons horizontally
        instead of vertically.
    """
    def render(self):
            """Outputs radios"""
            return mark_safe(u'\n'.join([u'%s\n' % w for w in self]))


class HorizRadioSelect(RadioSelect):
    renderer = HorizRadioRenderer


class HorizCheckboxSelect(CheckboxSelectMultiple):

    def render(self, *args, **kwargs):
        output = super(HorizCheckboxSelect, self).render(*args, **kwargs)
        return mark_safe(output.replace(u'<ul>', u'').replace(u'</ul>', u'').replace(u'<li>', u'').replace(u'</li>', u''))
