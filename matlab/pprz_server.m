function cfg = pprz_server(varargin)
%PPRZ_SERVER Logbook server, login and cache folder used by pprz_fetch, pprz_download, pprz_upload and pprz_logbook.
%   pprz_server('https://logs.example.org')          the server; the first call that needs it signs in through the
%                                                      browser (pprz_login), for a week
%   pprz_server('https://logs.example.org', 'KEY')   store an API key from My account > API keys instead
%   pprz_server('login')                              sign in through the browser now (pprz_login)
%   pprz_server('logout')                             revoke and forget the stored key ('clear' does the same)
%   pprz_server('cache', folder)                      where downloads are kept ('' returns to the default)
%   cfg = pprz_server()                               server, key, expires_at, user, source and cache
%   Shared with the Python package mavlab-logs (same login, same cache), in config.json of pprz.config('dir'),
%   readable by you only. LOGDB_SERVER and LOGDB_API_KEY in the environment take precedence, as for upload_logs.py.
    if nargin == 1 && any(strcmpi(varargin{1}, {'clear', 'logout'}))
        cfg = pprz_server();
        stored = entry(cfg.server);
        if ~isempty(stored)
            try
                pprz.http('DELETE', [cfg.server '/api/key'], [], stored.key);  % revoke it on the server
            catch
            end
            pprz.config('forget', cfg.server);
        end
    elseif nargin == 1 && strcmpi(varargin{1}, 'login')
        pprz_login();
    elseif nargin == 2 && strcmpi(varargin{1}, 'cache')
        c = pprz.config();
        c.cache = char(varargin{2});
        pprz.config('save', c);
    elseif nargin >= 1
        url = normal(varargin{1});
        c = pprz.config();
        c.default = url;
        pprz.config('save', c);
        if nargin >= 2
            key = char(varargin{2});
            [me, status] = pprz.http('GET', [url '/api/me'], [], key);
            if status ~= 200
                error('pprz_server:key', 'The server refused this key (HTTP %d)', status);
            end
            pprz.config('store', url, key, '', me.user.display_name);
        end
    end
    cfg = current();
    if nargout == 0 && nargin == 0
        key = cfg.key;
        if numel(key) > 8
            key = [key(1:6) '…'];
        end
        if ~isempty(cfg.expires_at)
            key = sprintf('%s (browser login of %s, until %s)', key, cfg.user, cfg.expires_at);
        end
        fprintf('Server: %s\nAPI key: %s\nCache: %s\n', blank(cfg.server), blank(key), cfg.cache);
    end
    if nargout == 0
        clear cfg
    end
end

function cfg = current()
    c = pprz.config();
    cfg = struct('server', '', 'key', '', 'expires_at', '', 'user', '', 'source', '', 'cache', pprz.config('cache'));
    env_server = getenv('LOGDB_SERVER');
    if ~isempty(env_server)
        cfg.server = normal(env_server);
    elseif ~isempty(c.default)
        cfg.server = c.default;
    end
    if isempty(cfg.server)
        return
    end
    env_key = getenv('LOGDB_API_KEY');
    if ~isempty(env_key) && (isempty(env_server) || strcmp(normal(env_server), cfg.server))
        cfg.key = env_key;
        cfg.source = 'environment';
        return
    end
    stored = entry(cfg.server);
    if ~isempty(stored) && ~isempty(stored.key) && ~expired(stored.expires_at)
        cfg.key = stored.key;
        cfg.expires_at = stored.expires_at;
        cfg.user = stored.user;
        cfg.source = 'stored';
    end
end

function s = entry(server)
    s = [];
    c = pprz.config();
    for k = 1:numel(c.servers)
        if strcmp(c.servers{k}.server, server)
            s = c.servers{k};
        end
    end
end

function tf = expired(stamp)
    tf = false;
    if ~isempty(stamp)
        when = datetime(stamp, 'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss''Z''', 'TimeZone', 'UTC');
        tf = when - datetime('now', 'TimeZone', 'UTC') < seconds(60);
    end
end

function url = normal(url)
    url = regexprep(strtrim(char(url)), '/+$', '');
    if isempty(regexp(url, '^https?://[^/\s]+', 'once'))
        error('pprz_server:url', 'The server must start with https:// (or http://), e.g. https://logs.example.org');
    end
end

function s = blank(s)
    if isempty(s)
        s = '(not set)';
    end
end
