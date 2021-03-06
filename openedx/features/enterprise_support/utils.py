"""
Utility methods for Enterprise
"""


import json

from crum import get_current_request
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils.translation import ugettext as _
from edx_django_utils.cache import TieredCache, get_cache_key
from enterprise.models import EnterpriseCustomerUser
from social_django.models import UserSocialAuth

import third_party_auth
from lms.djangoapps.branding.api import get_privacy_url
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers
from openedx.core.djangoapps.user_authn.cookies import standard_cookie_settings
from openedx.core.djangolib.markup import HTML, Text
from student.helpers import get_next_url_for_login_page


def get_data_consent_share_cache_key(user_id, course_id):
    """
        Returns cache key for data sharing consent needed against user_id and course_id
    """

    return get_cache_key(type='data_sharing_consent_needed', user_id=user_id, course_id=course_id)


def clear_data_consent_share_cache(user_id, course_id):
    """
        clears data_sharing_consent_needed cache
    """
    consent_cache_key = get_data_consent_share_cache_key(user_id, course_id)
    TieredCache.delete_all_tiers(consent_cache_key)


def update_logistration_context_for_enterprise(request, context, enterprise_customer):
    """
    Take the processed context produced by the view, determine if it's relevant
    to a particular Enterprise Customer, and update it to include that customer's
    enterprise metadata.

     Arguments:
         request (HttpRequest): The request for the logistration page.
         context (dict): Context for logistration page.
         enterprise_customer (dict): data for enterprise customer

    """
    sidebar_context = {}
    if enterprise_customer:
        sidebar_context = get_enterprise_sidebar_context(enterprise_customer)

    if sidebar_context:
        context['data']['registration_form_desc']['fields'] = enterprise_fields_only(
            context['data']['registration_form_desc']
        )
        context.update(sidebar_context)
        context['enable_enterprise_sidebar'] = True
        context['data']['hide_auth_warnings'] = True
        context['data']['enterprise_name'] = enterprise_customer['name']
    else:
        context['enable_enterprise_sidebar'] = False

    update_third_party_auth_context_for_enterprise(request, context, enterprise_customer)


def get_enterprise_sidebar_context(enterprise_customer):
    """
    Get context information for enterprise sidebar for the given enterprise customer.

    Enterprise Sidebar Context has the following key-value pairs.
    {
        'enterprise_name': 'Enterprise Name',
        'enterprise_logo_url': 'URL of the enterprise logo image',
        'enterprise_branded_welcome_string': 'Human readable welcome message customized for the enterprise',
        'platform_welcome_string': 'Human readable welcome message for an enterprise learner',
    }
    """
    platform_name = configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)

    branding_configuration = enterprise_customer.get('branding_configuration', {})
    logo_url = branding_configuration.get('logo', '') if isinstance(branding_configuration, dict) else ''

    branded_welcome_template = configuration_helpers.get_value(
        'ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE',
        settings.ENTERPRISE_SPECIFIC_BRANDED_WELCOME_TEMPLATE
    )

    branded_welcome_string = Text(branded_welcome_template).format(
        start_bold=HTML('<b>'),
        end_bold=HTML('</b>'),
        line_break=HTML('<br/>'),
        enterprise_name=enterprise_customer['name'],
        platform_name=platform_name,
        privacy_policy_link_start=HTML("<a href='{pp_url}' rel='noopener' target='_blank'>").format(
            pp_url=get_privacy_url()
        ),
        privacy_policy_link_end=HTML("</a>"),
    )

    platform_welcome_template = configuration_helpers.get_value(
        'ENTERPRISE_PLATFORM_WELCOME_TEMPLATE',
        settings.ENTERPRISE_PLATFORM_WELCOME_TEMPLATE
    )
    platform_welcome_string = platform_welcome_template.format(platform_name=platform_name)

    return {
        'enterprise_name': enterprise_customer['name'],
        'enterprise_logo_url': logo_url,
        'enterprise_branded_welcome_string': branded_welcome_string,
        'platform_welcome_string': platform_welcome_string,
    }


def enterprise_fields_only(fields):
    """
    Take the received field definition, and exclude those fields that we don't want
    to require if the user is going to be a member of an Enterprise Customer.
    """
    enterprise_exclusions = configuration_helpers.get_value(
        'ENTERPRISE_EXCLUDED_REGISTRATION_FIELDS',
        settings.ENTERPRISE_EXCLUDED_REGISTRATION_FIELDS
    )
    return [field for field in fields['fields'] if field['name'] not in enterprise_exclusions]


def update_third_party_auth_context_for_enterprise(request, context, enterprise_customer=None):
    """
    Return updated context of third party auth with modified for enterprise.

    Arguments:
        request (HttpRequest): The request for the logistration page.
        context (dict): Context for third party auth providers and auth pipeline.
        enterprise_customer (dict): data for enterprise customer

    Returns:
         context (dict): Updated context of third party auth with modified
         `errorMessage`.
    """
    if context['data']['third_party_auth']['errorMessage']:
        context['data']['third_party_auth']['errorMessage'] = Text(_(
            u'We are sorry, you are not authorized to access {platform_name} via this channel. '
            u'Please contact your learning administrator or manager in order to access {platform_name}.'
            u'{line_break}{line_break}'
            u'Error Details:{line_break}{error_message}')
        ).format(
            platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME),
            error_message=context['data']['third_party_auth']['errorMessage'],
            line_break=HTML('<br/>')
        )

    if enterprise_customer:
        context['data']['third_party_auth']['providers'] = []
        context['data']['third_party_auth']['secondaryProviders'] = []

    running_pipeline = third_party_auth.pipeline.get(request)
    if running_pipeline is not None:
        current_provider = third_party_auth.provider.Registry.get_from_pipeline(running_pipeline)
        if current_provider is not None and current_provider.skip_registration_form and enterprise_customer:
            # For enterprise (and later for everyone), we need to get explicit consent to the
            # Terms of service instead of auto submitting the registration form outright.
            context['data']['third_party_auth']['autoSubmitRegForm'] = False
            context['data']['third_party_auth']['autoRegisterWelcomeMessage'] = Text(_(
                'Thank you for joining {platform_name}. '
                'Just a couple steps before you start learning!')
            ).format(
                platform_name=configuration_helpers.get_value('PLATFORM_NAME', settings.PLATFORM_NAME)
            )
            context['data']['third_party_auth']['registerFormSubmitButtonText'] = _('Continue')

    return context


def handle_enterprise_cookies_for_logistration(request, response, context):
    """
    Helper method for setting or deleting enterprise cookies on logistration response.

    Arguments:
        request (HttpRequest): The request for the logistration page.
        response (HttpResponse): The response for the logistration page.
        context (dict): Context for logistration page.

    """
    # This cookie can be used for tests or minor features,
    # but should not be used for payment related or other critical work
    # since users can edit their cookies
    _set_experiments_is_enterprise_cookie(request, response, context['enable_enterprise_sidebar'])

    # Remove enterprise cookie so that subsequent requests show default login page.
    response.delete_cookie(
        configuration_helpers.get_value('ENTERPRISE_CUSTOMER_COOKIE_NAME', settings.ENTERPRISE_CUSTOMER_COOKIE_NAME),
        domain=configuration_helpers.get_value('BASE_COOKIE_DOMAIN', settings.BASE_COOKIE_DOMAIN),
    )


def _set_experiments_is_enterprise_cookie(request, response, experiments_is_enterprise):
    """ Sets the experiments_is_enterprise cookie on the response.
    This cookie can be used for tests or minor features,
    but should not be used for payment related or other critical work
    since users can edit their cookies
    """
    cookie_settings = standard_cookie_settings(request)

    response.set_cookie(
        'experiments_is_enterprise',
        json.dumps(experiments_is_enterprise),
        **cookie_settings
    )


def update_account_settings_context_for_enterprise(context, enterprise_customer, user):
    """
    Take processed context for account settings page and update it taking enterprise customer into account.

     Arguments:
        context (dict): Context for account settings page.
        enterprise_customer (dict): data for enterprise customer
        user (User): request user
    """
    enterprise_context = {
        'enterprise_name': enterprise_customer['name'] if enterprise_customer else None,
        'sync_learner_profile_data': _get_sync_learner_profile_data(enterprise_customer),
        'edx_support_url': configuration_helpers.get_value('SUPPORT_SITE_LINK', settings.SUPPORT_SITE_LINK),
        'enterprise_readonly_account_fields': {
            'fields': list(get_enterprise_readonly_account_fields(user))
        }
    }
    context.update(enterprise_context)


def get_enterprise_readonly_account_fields(user):
    """
    Returns a set of account fields that are read-only for enterprise users.
    """
    # TODO circular dependency between enterprise_support.api and enterprise_support.utils
    from openedx.features.enterprise_support.api import enterprise_customer_for_request
    enterprise_customer = enterprise_customer_for_request(get_current_request())

    enterprise_readonly_account_fields = list(settings.ENTERPRISE_READONLY_ACCOUNT_FIELDS)

    # if user has no `UserSocialAuth` record then allow to edit `fullname`
    # whether the `sync_learner_profile_data` is enabled or disabled
    user_social_auth_record = _user_has_social_auth_record(user, enterprise_customer)
    if not user_social_auth_record:
        enterprise_readonly_account_fields.remove('name')

    sync_learner_profile_data = _get_sync_learner_profile_data(enterprise_customer)
    return set(enterprise_readonly_account_fields) if sync_learner_profile_data else set()


def _user_has_social_auth_record(user, enterprise_customer):
    """
    Return True if a `UserSocialAuth` record exists for `user` False otherwise.
    """
    if enterprise_customer:
        identity_provider = third_party_auth.provider.Registry.get(
            provider_id=enterprise_customer['identity_provider'],
        )
        if identity_provider:
            return UserSocialAuth.objects.select_related('user').filter(
                provider=identity_provider.backend_name,
                user=user
            ).exists()

    return False


def _get_sync_learner_profile_data(enterprise_customer):
    """
    Returns whether the configuration of the given enterprise customer supports
    synching learner profile data.
    """
    if enterprise_customer:
        identity_provider = third_party_auth.provider.Registry.get(
            provider_id=enterprise_customer['identity_provider'],
        )
        if identity_provider:
            return identity_provider.sync_learner_profile_data

    return False


def get_enterprise_learner_generic_name(request):
    """
    Get a generic name concatenating the Enterprise Customer name and 'Learner'.

    ENT-924: Temporary solution for hiding potentially sensitive SSO names.
    When a more complete solution is put in place, delete this function and all of its uses.
    """
    # Prevent a circular import. This function makes sense to be in this module though. And see function description.
    from openedx.features.enterprise_support.api import enterprise_customer_for_request

    # ENT-2626: For 404 pages we don't need to perform these actions.
    if getattr(request, 'view_name', None) == '404':
        return

    enterprise_customer = enterprise_customer_for_request(request)

    return (
        enterprise_customer['name'] + 'Learner'
        if enterprise_customer and enterprise_customer['replace_sensitive_sso_username']
        else ''
    )


def is_enterprise_learner(user):
    """
    Check if the given user belongs to an enterprise.

    Arguments:
        user (User): Django User object.

    Returns:
        (bool): True if given user is an enterprise learner.
    """
    return EnterpriseCustomerUser.objects.filter(user_id=user.id).exists()


def get_enterprise_slug_login_url():
    """
    Return the enterprise slug login's URL (enterprise/login) if it exists otherwise None
    """
    try:
        return reverse('enterprise_slug_login')
    except NoReverseMatch:
        return None


def get_provider_login_url(request, provider_id, redirect_url=None):
    """
    Return the given provider's login URL.

    This method is here to avoid the importing of pipeline and student app in enterprise.
    """

    provider_login_url = third_party_auth.pipeline.get_login_url(
        provider_id,
        third_party_auth.pipeline.AUTH_ENTRY_LOGIN,
        redirect_url=redirect_url if redirect_url else get_next_url_for_login_page(request)
    )
    return provider_login_url
