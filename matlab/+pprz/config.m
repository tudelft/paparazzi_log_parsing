function out = config(action, varargin)
%CONFIG Settings shared with the Python package mavlab-logs: the stored login per server and the cache folder.
%   cfg = pprz.config()              read config.json: cache ('' = default) and servers (cell of structs with
%                                    server, key, expires_at and user of the last browser login per server)
%   pprz.config('save', cfg)         write it, readable by this user only (it holds API keys)
%   pprz.config('store', server, key, expires_at, user)   remember the key of a browser login
%   pprz.config('forget', server)    drop the key of a server
%   s = pprz.config('entry', server) the stored entry of a server ([] when none)
%   key = pprz.config('key', server) its key while it has not expired, else ''
%   url = pprz.config('normal', url) https://host without a trailing /; 'host' alone means https://host
%   folder = pprz.config('cache')    the cache folder in use: MAVLAB_LOGS_CACHE, the stored one, else
%                                    ~/.cache/mavlab-logs, ~/Library/Caches/mavlab-logs or %LOCALAPPDATA%\mavlab-logs\Cache
%   folder = pprz.config('dir')      MAVLAB_LOGS_CONFIG, else ~/.config/mavlab-logs,
%                                    ~/Library/Application Support/mavlab-logs or %APPDATA%\mavlab-logs
%   tag = pprz.config('tag', server) folder name for what is specific to a server (flight numbers, exports)
%   Keep the rules in step with client/mavlab_logs/settings.py.
    if nargin == 0
        action = 'load';
    end
    switch action
        case 'load'
            out = load_config();
        case 'save'
            save_config(varargin{1});
        case 'store'
            cfg = load_config();
            server = varargin{1};
            cfg.servers = cfg.servers(~cellfun(@(s) strcmp(s.server, server), cfg.servers));
            cfg.servers{end + 1} = struct('server', server, 'key', char(varargin{2}), ...
                'expires_at', char(varargin{3}), 'user', char(varargin{4}));
            save_config(cfg);
        case 'forget'
            cfg = load_config();
            cfg.servers = cfg.servers(~cellfun(@(s) strcmp(s.server, varargin{1}), cfg.servers));
            save_config(cfg);
        case 'cache'
            cfg = load_config();
            out = getenv('MAVLAB_LOGS_CACHE');
            if isempty(out)
                out = cfg.cache;
            end
            if isempty(out)
                if ispc
                    out = fullfile(env('LOCALAPPDATA', fullfile(home(), 'AppData', 'Local')), 'mavlab-logs', 'Cache');
                elseif ismac
                    out = fullfile(home(), 'Library', 'Caches', 'mavlab-logs');
                else
                    out = fullfile(env('XDG_CACHE_HOME', fullfile(home(), '.cache')), 'mavlab-logs');
                end
            end
        case 'entry'
            out = [];
            cfg = load_config();
            match = cellfun(@(s) strcmp(s.server, varargin{1}), cfg.servers);
            if any(match)
                out = cfg.servers{find(match, 1)};
            end
        case 'key'
            out = '';
            s = pprz.config('entry', varargin{1});
            if ~isempty(s) && ~expired(s.expires_at)
                out = s.key;
            end
        case 'normal'
            out = regexprep(strtrim(char(varargin{1})), '/+$', '');
            if ~isempty(out) && ~contains(out, '://')
                out = ['https://' out];
            end
            if isempty(regexp(out, '^https?://[^/\s]+', 'once'))
                error('pprz:server', 'Not a server URL: ''%s'' (e.g. https://logs.example.org)', out);
            end
        case 'dir'
            out = config_dir();
        case 'tag'
            rest = regexprep(char(varargin{1}), '^https?://', '');
            out = regexprep(regexprep(rest, '[^A-Za-z0-9.-]+', '_'), '^_+|_+$', '');
        otherwise
            error('pprz:config', 'Unknown action %s', action);
    end
end

function cfg = load_config()
    cfg = struct('cache', '', 'servers', {{}});
    file = fullfile(config_dir(), 'config.json');
    if ~isfile(file)
        return
    end
    try
        raw = jsondecode(fileread(file));
    catch err
        error('pprz:config', '%s is not valid JSON (%s); fix or delete it', file, err.message);
    end
    if isfield(raw, 'cache') && ischar(raw.cache)
        cfg.cache = raw.cache;
    end
    if isfield(raw, 'servers')
        list = raw.servers;
        if isstruct(list)
            list = num2cell(list);
        end
        for k = 1:numel(list)
            s = list{k};
            if ~isstruct(s) || ~isfield(s, 'server') || isempty(s.server)
                continue
            end
            item = struct('server', '', 'key', '', 'expires_at', '', 'user', '');
            for f = fieldnames(item)'
                if isfield(s, f{1}) && ischar(s.(f{1}))
                    item.(f{1}) = s.(f{1});
                end
            end
            cfg.servers{end + 1} = item;
        end
    end
end

function save_config(cfg)
    folder = config_dir();
    if ~isfolder(folder)
        mkdir(folder);
    end
    out = struct('version', 1, 'cache', char(cfg.cache));
    out.servers = reshape(cfg.servers, 1, []);  % a cell array is written as a JSON list, also with one entry
    file = fullfile(folder, 'config.json');
    temp = [tempname(folder) '.tmp'];
    cleanup = onCleanup(@() delete_if(temp));
    fid = fopen(temp, 'w');
    if fid < 0
        error('pprz:config', 'Cannot write %s', temp);
    end
    if isunix
        system(['chmod 600 "' temp '"']);  % readable by this user only, before the key is written
    end
    fwrite(fid, jsonencode(out, 'PrettyPrint', true), 'char');
    fclose(fid);
    movefile(temp, file, 'f');
end

function tf = expired(stamp)
    tf = false;
    if ~isempty(stamp)
        when = datetime(stamp, 'InputFormat', 'yyyy-MM-dd''T''HH:mm:ss''Z''', 'TimeZone', 'UTC');
        tf = when - datetime('now', 'TimeZone', 'UTC') < seconds(60);
    end
end

function delete_if(file)
    if isfile(file)
        delete(file);
    end
end

function folder = config_dir()
    folder = getenv('MAVLAB_LOGS_CONFIG');
    if ~isempty(folder)
        return
    end
    if ispc
        folder = fullfile(env('APPDATA', fullfile(home(), 'AppData', 'Roaming')), 'mavlab-logs');
    elseif ismac
        folder = fullfile(home(), 'Library', 'Application Support', 'mavlab-logs');
    else
        folder = fullfile(env('XDG_CONFIG_HOME', fullfile(home(), '.config')), 'mavlab-logs');
    end
end

function h = home()
    if ispc
        h = getenv('USERPROFILE');
    else
        h = getenv('HOME');
    end
end

function v = env(name, default)
    v = getenv(name);
    if isempty(v)
        v = default;
    end
end
