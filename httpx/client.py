import functools
import typing
import warnings
from types import TracebackType

import hstspreload

from .auth import Auth, AuthTypes, BasicAuth, FunctionAuth
from .backends.base import ConcurrencyBackend
from .config import (
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_POOL_LIMITS,
    DEFAULT_TIMEOUT_CONFIG,
    UNSET,
    CertTypes,
    PoolLimits,
    ProxiesTypes,
    Proxy,
    Timeout,
    TimeoutTypes,
    UnsetType,
    VerifyTypes,
)
from .content_streams import ContentStream
from .dispatch.asgi import ASGIDispatch
from .dispatch.base import AsyncDispatcher
from .dispatch.connection_pool import ConnectionPool
from .dispatch.proxy_http import HTTPProxy
from .exceptions import (
    HTTPError,
    InvalidURL,
    RedirectLoop,
    RequestBodyUnavailable,
    TooManyRedirects,
)
from .models import (
    URL,
    Cookies,
    CookieTypes,
    Headers,
    HeaderTypes,
    Origin,
    QueryParams,
    QueryParamTypes,
    Request,
    RequestData,
    RequestFiles,
    Response,
    URLTypes,
)
from .status_codes import codes
from .utils import NetRCInfo, get_environment_proxies, get_logger

logger = get_logger(__name__)


class AsyncClient:
    """
    An asynchronous HTTP client, with connection pooling, HTTP/2, redirects,
    cookie persistence, etc.

    Usage:

    ```python
    >>> client = httpx.AsyncClient()
    >>> response = client.get('https://example.org')
    ```

    **Parameters:**

    * **auth** - *(optional)* An authentication class to use when sending
    requests.
    * **params** - *(optional)* Query parameters to include in request URLs, as
    a string, dictionary, or list of two-tuples.
    * **headers** - *(optional)* Dictionary of HTTP headers to include when
    sending requests.
    * **cookies** - *(optional)* Dictionary of Cookie items to include when
    sending requests.
    * **verify** - *(optional)* SSL certificates (a.k.a CA bundle) used to
    verify the identity of requested hosts. Either `True` (default CA bundle),
    a path to an SSL certificate file, or `False` (disable verification).
    * **cert** - *(optional)* An SSL certificate used by the requested host
    to authenticate the client. Either a path to an SSL certificate file, or
    two-tuple of (certificate file, key file), or a three-tuple of (certificate
    file, key file, password).
    * **http2** - *(optional)* A boolean indicating if HTTP/2 support should be
    enabled. Defaults to `False`.
    * **proxies** - *(optional)* A dictionary mapping HTTP protocols to proxy
    URLs.
    * **timeout** - *(optional)* The timeout configuration to use when sending
    requests.
    * **pool_limits** - *(optional)* The connection pool configuration to use
    when determining the maximum number of concurrently open HTTP connections.
    * **max_redirects** - *(optional)* The maximum number of redirect responses
    that should be followed.
    * **base_url** - *(optional)* A URL to use as the base when building
    request URLs.
    * **dispatch** - *(optional)* A dispatch class to use for sending requests
    over the network.
    * **app** - *(optional)* An ASGI application to send requests to,
    rather than sending actual network requests.
    * **backend** - *(optional)* A concurrency backend to use when issuing
    async requests. Either 'auto', 'asyncio', 'trio', or a `ConcurrencyBackend`
    instance. Defaults to 'auto', for autodetection.
    * **trust_env** - *(optional)* Enables or disables usage of environment
    variables for configuration.
    * **uds** - *(optional)* A path to a Unix domain socket to connect through.
    """

    def __init__(
        self,
        *,
        auth: AuthTypes = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        verify: VerifyTypes = True,
        cert: CertTypes = None,
        http2: bool = False,
        proxies: ProxiesTypes = None,
        timeout: TimeoutTypes = DEFAULT_TIMEOUT_CONFIG,
        pool_limits: PoolLimits = DEFAULT_POOL_LIMITS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        base_url: URLTypes = None,
        dispatch: AsyncDispatcher = None,
        app: typing.Callable = None,
        backend: typing.Union[str, ConcurrencyBackend] = "auto",
        trust_env: bool = True,
        uds: str = None,
    ):
        if base_url is None:
            self.base_url = URL("", allow_relative=True)
        else:
            self.base_url = URL(base_url)

        if params is None:
            params = {}

        if proxies is None and trust_env:
            proxies = typing.cast(ProxiesTypes, get_environment_proxies())

        self.auth = auth
        self._params = QueryParams(params)
        self._headers = Headers(headers)
        self._cookies = Cookies(cookies)
        self.timeout = Timeout(timeout)
        self.max_redirects = max_redirects
        self.trust_env = trust_env
        self.netrc = NetRCInfo()

        self.dispatch = self.init_dispatch(
            verify=verify,
            cert=cert,
            http2=http2,
            pool_limits=pool_limits,
            dispatch=dispatch,
            app=app,
            backend=backend,
            trust_env=trust_env,
            uds=uds,
        )

        if proxies is None and trust_env:
            proxies = typing.cast(ProxiesTypes, get_environment_proxies())

        self.proxies: typing.Dict[str, AsyncDispatcher] = self.proxies_to_dispatchers(
            proxies,
            verify=verify,
            cert=cert,
            http2=http2,
            pool_limits=pool_limits,
            backend=backend,
            trust_env=trust_env,
        )

    def init_dispatch(
        self,
        verify: VerifyTypes = True,
        cert: CertTypes = None,
        http2: bool = False,
        pool_limits: PoolLimits = DEFAULT_POOL_LIMITS,
        dispatch: AsyncDispatcher = None,
        app: typing.Callable = None,
        backend: typing.Union[str, ConcurrencyBackend] = "auto",
        trust_env: bool = True,
        uds: str = None,
    ) -> AsyncDispatcher:
        if dispatch is not None:
            return dispatch

        if app is not None:
            return ASGIDispatch(app=app)

        return ConnectionPool(
            verify=verify,
            cert=cert,
            http2=http2,
            pool_limits=pool_limits,
            backend=backend,
            trust_env=trust_env,
            uds=uds,
        )

    def init_proxy_dispatch(
        self,
        proxy: Proxy,
        verify: VerifyTypes = True,
        cert: CertTypes = None,
        http2: bool = False,
        pool_limits: PoolLimits = DEFAULT_POOL_LIMITS,
        backend: typing.Union[str, ConcurrencyBackend] = "auto",
        trust_env: bool = True,
    ) -> AsyncDispatcher:
        return HTTPProxy(
            proxy_url=proxy.url,
            proxy_headers=proxy.headers,
            proxy_mode=proxy.mode,
            verify=verify,
            cert=cert,
            http2=http2,
            pool_limits=pool_limits,
            backend=backend,
            trust_env=trust_env,
        )

    def proxies_to_dispatchers(
        self,
        proxies: typing.Optional[ProxiesTypes],
        verify: VerifyTypes,
        cert: typing.Optional[CertTypes],
        http2: bool,
        pool_limits: PoolLimits,
        backend: typing.Union[str, ConcurrencyBackend],
        trust_env: bool,
    ) -> typing.Dict[str, AsyncDispatcher]:
        if proxies is None:
            return {}
        elif isinstance(proxies, (str, URL, Proxy)):
            proxy = Proxy(url=proxies) if isinstance(proxies, (str, URL)) else proxies
            return {
                "all": self.init_proxy_dispatch(
                    proxy=proxy,
                    verify=verify,
                    cert=cert,
                    pool_limits=pool_limits,
                    backend=backend,
                    trust_env=trust_env,
                    http2=http2,
                )
            }
        elif isinstance(proxies, AsyncDispatcher):  # pragma: nocover
            return {"all": proxies}
            # We're supporting this style for now, but we'll want to deprecate it.
            #
            # raise RuntimeError(
            #     "Passing a AsyncDispatcher instance to 'proxies=' is no longer
            #     supported. Use `httpx.Proxy() instead.`"
            # )
        else:
            new_proxies = {}
            for key, value in proxies.items():
                if isinstance(value, (str, URL, Proxy)):
                    proxy = Proxy(url=value) if isinstance(value, (str, URL)) else value
                    new_proxies[str(key)] = self.init_proxy_dispatch(
                        proxy=proxy,
                        verify=verify,
                        cert=cert,
                        pool_limits=pool_limits,
                        backend=backend,
                        trust_env=trust_env,
                        http2=http2,
                    )
                elif isinstance(value, AsyncDispatcher):  # pragma: nocover
                    new_proxies[str(key)] = value
                    # We're supporting this style for now, but we'll want to
                    # deprecate it.
                    #
                    # raise RuntimeError(
                    #     "Passing a AsyncDispatcher instance to 'proxies=' is "
                    #     "no longer supported. Use `httpx.Proxy() instead.`"
                    # )
            return new_proxies

    @property
    def headers(self) -> Headers:
        """
        HTTP headers to include when sending requests.
        """
        return self._headers

    @headers.setter
    def headers(self, headers: HeaderTypes) -> None:
        self._headers = Headers(headers)

    @property
    def cookies(self) -> Cookies:
        """
        Cookie values to include when sending requests.
        """
        return self._cookies

    @cookies.setter
    def cookies(self, cookies: CookieTypes) -> None:
        self._cookies = Cookies(cookies)

    @property
    def params(self) -> QueryParams:
        """
        Query parameters to include in the URL when sending requests.
        """
        return self._params

    @params.setter
    def params(self, params: QueryParamTypes) -> None:
        self._params = QueryParams(params)

    def stream(
        self,
        method: str,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
    ) -> "StreamContextManager":
        request = self.build_request(
            method=method,
            url=url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
        )
        return StreamContextManager(
            client=self,
            request=request,
            auth=auth,
            allow_redirects=allow_redirects,
            timeout=timeout,
        )

    def build_request(
        self,
        method: str,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
    ) -> Request:
        """
        Build and return a request instance.
        """
        url = self.merge_url(url)
        headers = self.merge_headers(headers)
        cookies = self.merge_cookies(cookies)
        params = self.merge_queryparams(params)
        return Request(
            method,
            url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
        )

    def merge_url(self, url: URLTypes) -> URL:
        """
        Merge a URL argument together with any 'base_url' on the client,
        to create the URL used for the outgoing request.
        """
        url = self.base_url.join(relative_url=url)
        if url.scheme == "http" and hstspreload.in_hsts_preload(url.host):
            port = None if url.port == 80 else url.port
            url = url.copy_with(scheme="https", port=port)
        return url

    def merge_cookies(
        self, cookies: CookieTypes = None
    ) -> typing.Optional[CookieTypes]:
        """
        Merge a cookies argument together with any cookies on the client,
        to create the cookies used for the outgoing request.
        """
        if cookies or self.cookies:
            merged_cookies = Cookies(self.cookies)
            merged_cookies.update(cookies)
            return merged_cookies
        return cookies

    def merge_headers(
        self, headers: HeaderTypes = None
    ) -> typing.Optional[HeaderTypes]:
        """
        Merge a headers argument together with any headers on the client,
        to create the headers used for the outgoing request.
        """
        if headers or self.headers:
            merged_headers = Headers(self.headers)
            merged_headers.update(headers)
            return merged_headers
        return headers

    def merge_queryparams(
        self, params: QueryParamTypes = None
    ) -> typing.Optional[QueryParamTypes]:
        """
        Merge a queryparams argument together with any queryparams on the client,
        to create the queryparams used for the outgoing request.
        """
        if params or self.params:
            merged_queryparams = QueryParams(self.params)
            merged_queryparams.update(params)
            return merged_queryparams
        return params

    def build_auth(self, request: Request, auth: AuthTypes = None) -> Auth:
        auth = self.auth if auth is None else auth

        if auth is not None:
            if isinstance(auth, tuple):
                return BasicAuth(username=auth[0], password=auth[1])
            elif isinstance(auth, Auth):
                return auth
            elif callable(auth):
                return FunctionAuth(func=auth)
            raise TypeError('Invalid "auth" argument.')

        username, password = request.url.username, request.url.password
        if username or password:
            return BasicAuth(username=username, password=password)

        if self.trust_env and "Authorization" not in request.headers:
            credentials = self.netrc.get_credentials(request.url.authority)
            if credentials is not None:
                return BasicAuth(username=credentials[0], password=credentials[1])

        return Auth()

    def build_redirect_request(self, request: Request, response: Response) -> Request:
        """
        Given a request and a redirect response, return a new request that
        should be used to effect the redirect.
        """
        method = self.redirect_method(request, response)
        url = self.redirect_url(request, response)
        headers = self.redirect_headers(request, url, method)
        stream = self.redirect_stream(request, method)
        cookies = Cookies(self.cookies)
        return Request(
            method=method, url=url, headers=headers, cookies=cookies, stream=stream
        )

    def redirect_method(self, request: Request, response: Response) -> str:
        """
        When being redirected we may want to change the method of the request
        based on certain specs or browser behavior.
        """
        method = request.method

        # https://tools.ietf.org/html/rfc7231#section-6.4.4
        if response.status_code == codes.SEE_OTHER and method != "HEAD":
            method = "GET"

        # Do what the browsers do, despite standards...
        # Turn 302s into GETs.
        if response.status_code == codes.FOUND and method != "HEAD":
            method = "GET"

        # If a POST is responded to with a 301, turn it into a GET.
        # This bizarre behaviour is explained in 'requests' issue 1704.
        if response.status_code == codes.MOVED_PERMANENTLY and method == "POST":
            method = "GET"

        return method

    def redirect_url(self, request: Request, response: Response) -> URL:
        """
        Return the URL for the redirect to follow.
        """
        location = response.headers["Location"]

        url = URL(location, allow_relative=True)

        # Facilitate relative 'Location' headers, as allowed by RFC 7231.
        # (e.g. '/path/to/resource' instead of 'http://domain.tld/path/to/resource')
        if url.is_relative_url:
            url = request.url.join(url)

        # Attach previous fragment if needed (RFC 7231 7.1.2)
        if request.url.fragment and not url.fragment:
            url = url.copy_with(fragment=request.url.fragment)

        return url

    def redirect_headers(self, request: Request, url: URL, method: str) -> Headers:
        """
        Return the headers that should be used for the redirect request.
        """
        headers = Headers(request.headers)

        if Origin(url) != Origin(request.url):
            # Strip Authorization headers when responses are redirected away from
            # the origin.
            headers.pop("Authorization", None)
            headers["Host"] = url.authority

        if method != request.method and method == "GET":
            # If we've switch to a 'GET' request, then strip any headers which
            # are only relevant to the request body.
            headers.pop("Content-Length", None)
            headers.pop("Transfer-Encoding", None)

        # We should use the client cookie store to determine any cookie header,
        # rather than whatever was on the original outgoing request.
        headers.pop("Cookie", None)

        return headers

    def redirect_stream(
        self, request: Request, method: str
    ) -> typing.Optional[ContentStream]:
        """
        Return the body that should be used for the redirect request.
        """
        if method != request.method and method == "GET":
            return None

        if not request.stream.can_replay():
            raise RequestBodyUnavailable(
                "Got a redirect response, but the request body was streaming "
                "and is no longer available."
            )

        return request.stream

    def dispatcher_for_url(self, url: URL) -> AsyncDispatcher:
        """
        Returns the AsyncDispatcher instance that should be used for a given URL.
        This will either be the standard connection pool, or a proxy.
        """
        if self.proxies:
            is_default_port = (url.scheme == "http" and url.port == 80) or (
                url.scheme == "https" and url.port == 443
            )
            hostname = f"{url.host}:{url.port}"
            proxy_keys = (
                f"{url.scheme}://{hostname}",
                f"{url.scheme}://{url.host}" if is_default_port else None,
                f"all://{hostname}",
                f"all://{url.host}" if is_default_port else None,
                url.scheme,
                "all",
            )
            for proxy_key in proxy_keys:
                if proxy_key and proxy_key in self.proxies:
                    dispatcher = self.proxies[proxy_key]
                    return dispatcher

        return self.dispatch

    async def request(
        self,
        method: str,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        if cert is not None:  # pragma: nocover
            raise RuntimeError(
                "Passing a 'cert' argument when making a request on a client "
                "is not supported anymore. Instantiate a new client instead, "
                "passing any 'cert' arguments to the client itself."
            )

        if verify is not None:  # pragma: nocover
            raise RuntimeError(
                "Passing a 'verify' argument when making a request on a client "
                "is not supported anymore. Instantiate a new client instead, "
                "passing any 'verify' arguments to the client itself."
            )

        if trust_env is not None:  # pragma: nocover
            raise RuntimeError(
                "Passing a 'trust_env' argument when making a request on a client "
                "is not supported anymore. Instantiate a new client instead, "
                "passing any 'trust_env' argument to the client itself."
            )

        if stream:  # pragma: nocover
            warnings.warn(
                "The 'stream=True' argument is due to be deprecated. "
                "Use 'async with client.stream(method, url, ...) as response' instead."
            )

        request = self.build_request(
            method=method,
            url=url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
        )
        response = await self.send(
            request,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            timeout=timeout,
        )
        return response

    async def send(
        self,
        request: Request,
        *,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
    ) -> Response:
        if request.url.scheme not in ("http", "https"):
            raise InvalidURL('URL scheme must be "http" or "https".')

        timeout = self.timeout if isinstance(timeout, UnsetType) else Timeout(timeout)

        auth = self.build_auth(request, auth)

        response = await self.send_handling_redirects(
            request, auth=auth, timeout=timeout, allow_redirects=allow_redirects,
        )

        if not stream:
            try:
                await response.aread()
            finally:
                await response.aclose()

        return response

    async def send_handling_redirects(
        self,
        request: Request,
        auth: Auth,
        timeout: Timeout,
        allow_redirects: bool = True,
        history: typing.List[Response] = None,
    ) -> Response:
        if history is None:
            history = []

        while True:
            if len(history) > self.max_redirects:
                raise TooManyRedirects()
            if request.url in (response.url for response in history):
                raise RedirectLoop()

            response = await self.send_handling_auth(
                request, auth=auth, timeout=timeout, history=history
            )
            response.history = list(history)

            if not response.is_redirect:
                return response

            await response.aread()
            request = self.build_redirect_request(request, response)
            history = history + [response]

            if not allow_redirects:
                response.call_next = functools.partial(
                    self.send_handling_redirects,
                    request=request,
                    auth=auth,
                    timeout=timeout,
                    allow_redirects=False,
                    history=history,
                )
                return response

    async def send_handling_auth(
        self,
        request: Request,
        history: typing.List[Response],
        auth: Auth,
        timeout: Timeout,
    ) -> Response:
        auth_flow = auth(request)
        request = next(auth_flow)
        while True:
            response = await self.send_single_request(request, timeout)
            try:
                next_request = auth_flow.send(response)
            except StopIteration:
                return response
            except BaseException as exc:
                await response.aclose()
                raise exc from None
            else:
                response.history = list(history)
                await response.aread()
                request = next_request
                history.append(response)

    async def send_single_request(
        self, request: Request, timeout: Timeout,
    ) -> Response:
        """
        Sends a single request, without handling any redirections.
        """

        dispatcher = self.dispatcher_for_url(request.url)

        try:
            response = await dispatcher.send(request, timeout=timeout)
        except HTTPError as exc:
            # Add the original request to any HTTPError unless
            # there'a already a request attached in the case of
            # a ProxyError.
            if exc.request is None:
                exc.request = request
            raise

        self.cookies.extract_cookies(response)

        status = f"{response.status_code} {response.reason_phrase}"
        response_line = f"{response.http_version} {status}"
        logger.debug(f'HTTP Request: {request.method} {request.url} "{response_line}"')

        return response

    async def get(
        self,
        url: URLTypes,
        *,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "GET",
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def options(
        self,
        url: URLTypes,
        *,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "OPTIONS",
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def head(
        self,
        url: URLTypes,
        *,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = False,  # NOTE: Differs to usual default.
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "HEAD",
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def post(
        self,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "POST",
            url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def put(
        self,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "PUT",
            url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def patch(
        self,
        url: URLTypes,
        *,
        data: RequestData = None,
        files: RequestFiles = None,
        json: typing.Any = None,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "PATCH",
            url,
            data=data,
            files=files,
            json=json,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def delete(
        self,
        url: URLTypes,
        *,
        params: QueryParamTypes = None,
        headers: HeaderTypes = None,
        cookies: CookieTypes = None,
        stream: bool = False,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        cert: CertTypes = None,
        verify: VerifyTypes = None,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        trust_env: bool = None,
    ) -> Response:
        return await self.request(
            "DELETE",
            url,
            params=params,
            headers=headers,
            cookies=cookies,
            stream=stream,
            auth=auth,
            allow_redirects=allow_redirects,
            verify=verify,
            cert=cert,
            timeout=timeout,
            trust_env=trust_env,
        )

    async def aclose(self) -> None:
        await self.dispatch.close()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(
        self,
        exc_type: typing.Type[BaseException] = None,
        exc_value: BaseException = None,
        traceback: TracebackType = None,
    ) -> None:
        await self.aclose()


class StreamContextManager:
    def __init__(
        self,
        client: AsyncClient,
        request: Request,
        *,
        auth: AuthTypes = None,
        allow_redirects: bool = True,
        timeout: typing.Union[TimeoutTypes, UnsetType] = UNSET,
        close_client: bool = False,
    ) -> None:
        self.client = client
        self.request = request
        self.auth = auth
        self.allow_redirects = allow_redirects
        self.timeout = timeout
        self.close_client = close_client

    async def __aenter__(self) -> "Response":
        self.response = await self.client.send(
            request=self.request,
            auth=self.auth,
            allow_redirects=self.allow_redirects,
            timeout=self.timeout,
            stream=True,
        )
        return self.response

    async def __aexit__(
        self,
        exc_type: typing.Type[BaseException] = None,
        exc_value: BaseException = None,
        traceback: TracebackType = None,
    ) -> None:
        await self.response.aclose()
        if self.close_client:
            await self.client.aclose()


# For compatibility with 0.9.x.
Client = AsyncClient
