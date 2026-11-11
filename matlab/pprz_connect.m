function c = pprz_connect(server, key)
%PPRZ_CONNECT Connect to the logbook server for pprz_fetch, pprz_upload, pprz_logbook, pprz_revoke_all.
%   pprz_connect('https://logs.example.org')      the server ('logs.example.org' works too)
%   pprz_connect(server, 'logdb_…')               with an API key from My account > API keys
%   c = pprz_connect(...)                         server, user and where the key came from (source)
%   Without a key, LOGDB_API_KEY is used (unless LOGDB_SERVER names another server), else the key of the last
%   browser login to this server, else the browser opens: sign in, check that it shows the code printed here and
%   press Allow. That key lasts a week and is shared with the Python package mavlab-logs. Values taken from the
%   environment are announced and named in errors.
    if nargin < 1 || isempty(server)
        error('pprz:server', 'Give the server: pprz_connect(''https://logs.example.org'')');
    end
    if nargin < 2
        key = '';
    end
    env_server = getenv('LOGDB_SERVER');
    c = struct('server', pprz.config('normal', server), 'key', char(key), 'source', 'argument', 'user', [], ...
        'origin', '');
    if isempty(c.key)
        env_key = getenv('LOGDB_API_KEY');
        if ~isempty(env_key) && (isempty(env_server) || strcmp(pprz.config('normal', env_server), c.server))
            c.key = env_key;
            c.source = 'LOGDB_API_KEY';
            fprintf('Using LOGDB_API_KEY from the environment.\n');
        else
            c.key = pprz.config('key', c.server);  % '' when there is none or it expired
            c.source = 'browser login';
        end
    end
    c.origin = ['server ' c.server];
    if strcmp(c.source, 'LOGDB_API_KEY')
        c.origin = [c.origin '; API key from the environment variable LOGDB_API_KEY'];
    else
        c.origin = [c.origin '; API key from the ' c.source];
    end
    if isempty(c.key)
        c.key = pprz.login(c.server);
    end
    [me, status] = whoami(c);
    if status == 401 && strcmp(c.source, 'browser login')  % expired or revoked on the server
        c.key = pprz.login(c.server);
        [me, status] = whoami(c);
    end
    if status ~= 200
        error('pprz:connect', 'The server refused the API key: HTTP %d (%s)', status, c.origin);
    end
    c.user = me.user;
    pprz.connection(c);
    fprintf('Connected to %s as %s.\n', c.server, c.user.display_name);
    if nargout == 0
        clear c
    end
end

function [me, status] = whoami(c)
    try
        [me, status] = pprz.http('GET', [c.server '/api/me'], [], c.key);
    catch err
        error('pprz:offline', 'Cannot reach the %s: %s', c.origin, err.message);
    end
end
