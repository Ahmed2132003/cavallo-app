"""
Views for Part P-026 — Business/Customer Profile CRUD.

This is the first genuinely IDOR-sensitive endpoint in the project
(architecture Section 5, rule 10). The pattern established here —
resolving "my own object" from `request.user`'s related profile,
NEVER from a URL- or body-supplied id — is the template every future
"my own content" endpoint (Products in Phase 5, Posts/Stories in
Phases 6-8) must copy, per this part's own handoff note.

BusinessProfileMeView.get_object()-equivalent logic (see
_get_own_profile below) always returns `request.user.business_profile`
via the OneToOneField's related_name. It never looks up a profile by
an id from the URL or the request body for either the Business or the
Customer "me" endpoints. This structurally eliminates the IDOR risk
for these two endpoints rather than relying solely on a permission
check that could later be misconfigured or bypassed — even if a client
sends `{"id": <someone else's profile id>, ...}` in a PATCH body, that
key is not a declared serializer field (see serializers.py) and is
silently dropped during validation, so it never reaches the service
layer at all.

BusinessProfilePublicView is the one deliberately public, read-only
exception: it looks up by an id in the URL on purpose, because it
exists specifically to let anyone view a business's public profile.
"""

from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import NotFound
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.cache import cache_get_or_set

from . import services
from .models import BusinessProfile, CustomerProfile
from .serializers import BusinessProfileSerializer, CustomerProfileSerializer


def _call_service(service_fn, **kwargs):
    """
    Calls a businesses.services function and converts its
    django.core.exceptions.ValidationError into
    rest_framework.exceptions.ValidationError, so DRF's exception
    handler (core/exceptions.py) can turn it into the project's
    standard {"error": {...}} envelope. Matches the exact
    catch/re-raise convention services.py's own module docstring
    documents and accounts/serializers.py already establishes
    elsewhere in this codebase (see its validate_password()) — the
    service layer itself stays plain-Python/DRF-agnostic on purpose.
    """
    try:
        return service_fn(**kwargs)
    except DjangoValidationError as exc:
        raise DRFValidationError(list(exc.messages))


class BusinessProfileMeView(APIView):
    """
    GET    /api/v1/businesses/me/  — return the authenticated Business
                                      user's own profile, or 404 if they
                                      haven't onboarded yet.
    POST   /api/v1/businesses/me/  — first-time onboarding: create the
                                      authenticated user's profile.
    PATCH  /api/v1/businesses/me/  — update the authenticated user's
                                      own profile (partial update).

    Authenticated only. There is no id in the URL for any of these
    three methods — see the module docstring for why that's the point.
    """

    permission_classes = [IsAuthenticated]

    def _get_own_profile_or_none(self, user):
        try:
            return user.business_profile
        except BusinessProfile.DoesNotExist:
            return None

    def get(self, request):
        profile = self._get_own_profile_or_none(request.user)
        if profile is None:
            raise NotFound(
                "You don't have a business profile yet. "
                "POST to this endpoint to create one first."
            )
        return Response(BusinessProfileSerializer(profile).data)

    def post(self, request):
        if self._get_own_profile_or_none(request.user) is not None:
            raise DRFValidationError(
                "You already have a business profile. Use PATCH to update it."
            )

        serializer = BusinessProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        # `category` is deliberately NOT accepted by
        # services.create_business_profile() (P-024's function keeps
        # its exact original signature per this part's own scope: "do
        # not restructure P-024's model/service wholesale"). It can
        # still be set at onboarding time as a convenience — create
        # the profile first via the unmodified P-024 service, then
        # apply category as a small additive follow-up save, all
        # inside the same request/response cycle. A Business that
        # skips it here can always add/change it later via PATCH.
        category = data.pop("category", None)

        profile = _call_service(
            services.create_business_profile, user=request.user, **data
        )
        if category is not None:
            profile.category = category
            profile.save(update_fields=["category"])

        return Response(BusinessProfileSerializer(profile).data, status=201)

    def patch(self, request):
        profile = self._get_own_profile_or_none(request.user)
        if profile is None:
            raise NotFound(
                "You don't have a business profile yet. "
                "POST to this endpoint to create one first."
            )

        # partial=True: PATCH semantics — only the fields the client
        # sent are validated/updated. Any `id`/`user` key in the body
        # is not a declared field on BusinessProfileSerializer and is
        # therefore silently ignored, never honored — see this
        # module's and serializers.py's docstrings.
        serializer = BusinessProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        profile = _call_service(
            services.update_business_profile,
            user=request.user,
            **serializer.validated_data,
        )

        # Part P-030: the public read (BusinessProfilePublicView.retrieve()
        # below) caches this profile under _business_profile_cache_key(id)
        # for up to BUSINESS_PROFILE_CACHE_TTL_SECONDS. Deleting it here -
        # instead of waiting out the TTL - makes the owner's own edit
        # visible on the very next public read, per this Part's own
        # acceptance criteria. Direct cache.delete() call, not
        # cache_get_or_set: the same sanctioned exception
        # categories/signals.py already established for P-025 (that
        # function's contract is "read-or-compute-and-store", it has no
        # invalidation mode, and delete is a different operation with no
        # ad hoc key string involved here either).
        cache.delete(_business_profile_cache_key(profile.id))

        return Response(BusinessProfileSerializer(profile).data)


BUSINESS_PROFILE_CACHE_TTL_SECONDS = 300  # 5 min, per architecture Section 16


def _business_profile_cache_key(pk):
    """Cache key convention for a Business Profile's public-read cache.

    Shared between BusinessProfilePublicView.retrieve() (which caches
    under it) and BusinessProfileMeView.patch() (which invalidates it)
    so the exact key format cannot drift between the two call sites.
    """
    return f"business_profile:{pk}"


class BusinessProfilePublicView(RetrieveAPIView):
    """
    GET /api/v1/businesses/{id}/ — public, read-only, no auth required.

    Genuinely public: any authenticated OR anonymous user can view.
    Serializes only public-appropriate fields — see
    BusinessProfileSerializer's own docstring for why the same
    serializer class used for the owner's /me/ view is safe to reuse
    here unchanged (no raw User fields, no internal
    verification-review notes are ever exposed by it).

    Part P-030: the read below is cached for
    BUSINESS_PROFILE_CACHE_TTL_SECONDS under
    _business_profile_cache_key(id) via core.cache.cache_get_or_set.
    BusinessProfileMeView.patch() above deletes this same key on a
    successful update, so an owner's own edit is never masked by a
    stale cached read.
    """

    queryset = BusinessProfile.objects.all()
    serializer_class = BusinessProfileSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def retrieve(self, request, *args, **kwargs):
        # get_object() still runs check_object_permissions() and can
        # raise Http404 — both happen only inside _compute(), i.e.
        # only on a cache miss, exactly like an uncached view would.
        pk = self.kwargs["pk"]
        cache_key = _business_profile_cache_key(pk)

        def _compute():
            instance = self.get_object()
            return self.get_serializer(instance).data

        data = cache_get_or_set(
            cache_key, _compute, ttl_seconds=BUSINESS_PROFILE_CACHE_TTL_SECONDS
        )
        return Response(data)


class CustomerProfileMeView(APIView):
    """
    GET/POST/PATCH /api/v1/customers/me/ — same IDOR-safe-by-construction
    pattern as BusinessProfileMeView above, for CustomerProfile. No
    public view exists for CustomerProfile, per this part's explicit
    scope (no public-facing use case for browsing customer profiles).
    """

    permission_classes = [IsAuthenticated]

    def _get_own_profile_or_none(self, user):
        try:
            return user.customer_profile
        except CustomerProfile.DoesNotExist:
            return None

    def get(self, request):
        profile = self._get_own_profile_or_none(request.user)
        if profile is None:
            raise NotFound(
                "You don't have a customer profile yet. "
                "POST to this endpoint to create one first."
            )
        return Response(CustomerProfileSerializer(profile).data)

    def post(self, request):
        if self._get_own_profile_or_none(request.user) is not None:
            raise DRFValidationError(
                "You already have a customer profile. Use PATCH to update it."
            )

        serializer = CustomerProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = _call_service(
            services.create_customer_profile,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(CustomerProfileSerializer(profile).data, status=201)

    def patch(self, request):
        profile = self._get_own_profile_or_none(request.user)
        if profile is None:
            raise NotFound(
                "You don't have a customer profile yet. "
                "POST to this endpoint to create one first."
            )

        serializer = CustomerProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        profile = _call_service(
            services.update_customer_profile,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(CustomerProfileSerializer(profile).data)
