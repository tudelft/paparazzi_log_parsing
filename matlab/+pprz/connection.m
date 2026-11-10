function c = connection(c)
%CONNECTION The connection made by pprz_connect, used by pprz.api, pprz_download and pprz_revoke_all.
%   c = pprz.connection()    server, key, source of the key ('argument', 'LOGDB_API_KEY' or 'browser login'),
%                            origin (server and key source, for messages), user and the cache folder
%   pprz.connection(c)       set it (pprz_connect, or after a new browser login)
    persistent current
    if nargin == 1
        current = rmfield(c, intersect(fieldnames(c), {'cache'}));
        clear c
        return
    end
    if isempty(current)
        error('pprz:connect', 'Not connected to a logbook server: call pprz_connect() first');
    end
    c = current;
    c.cache = pprz.config('cache');
end
