function n = pprz_revoke_all()
%PPRZ_REVOKE_ALL Revoke your API keys from browser logins (all computers, MATLAB and Python) on the connected server.
%   pprz_revoke_all()        the keys of browser logins; keys made on the account page keep working (revoke those there)
%   n = pprz_revoke_all()    the number of keys revoked
%   The browser login stored on this computer is forgotten; the next call that needs the server signs in again.
    c = pprz.connection();
    out = pprz.api('POST', '/api/keys/revoke-all', struct());
    n = out.revoked;
    stored = pprz.config('entry', c.server);
    if ~isempty(stored) && ~isempty(stored.expires_at)
        pprz.config('forget', c.server);
    end
    if strcmp(c.source, 'browser login')
        c.key = '';
        pprz.connection(c);
    end
    fprintf('Revoked %d API key(s) on %s.\n', n, c.server);
    if nargout == 0
        clear n
    end
end
