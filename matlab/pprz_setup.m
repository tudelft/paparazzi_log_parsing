function cfg = pprz_setup(varargin)
%PPRZ_SETUP Prepare this computer to open .data/.log files in MATLAB (pprz_load('LOG.data')), also offline.
%   pprz_setup                      check Python (with h5py and numpy) and the parser; download the parser of the
%                                   latest release when none is found (needs internet once, e.g. before the field)
%   pprz_setup('install', true)     also install h5py and numpy with pip (for this user) when they are missing
%   pprz_setup('download', true)    download the latest released parser even when one is found
%   pprz_setup('python', cmd)       use this Python command, e.g. 'C:\Python312\python.exe' or 'py -3'
%   pprz_setup('parser', path)      use this parser, e.g. a self-built cpp/build/paparazzi_log_parsing
%   cfg = pprz_setup('quiet', true) the settings without printing or downloading (used by pprz_load)
%   The parser is found in this order: the 'parser' setting, a self-built cpp/build (or cpp/build/Release), a
%   downloaded one. Settings are MATLAB preferences of this user (group 'pprz_local'); after setup everything
%   works without internet.
    opt = struct('install', false, 'download', false, 'python', '', 'parser', '', 'quiet', false);
    for k = 1:2:numel(varargin)
        opt.(lower(varargin{k})) = varargin{k + 1};
    end
    group = 'pprz_local';
    root = fileparts(fileparts(mfilename('fullpath')));  % repository root (matlab/..)
    if ~isempty(opt.python)
        setpref(group, 'python', char(opt.python));
    end
    if ~isempty(opt.parser)
        setpref(group, 'parser', char(opt.parser));
    end
    cfg = struct('python', '', 'parser', '', 'parser_version', '', 'script', ...
        fullfile(root, 'webui', 'matlab_export.py'), 'cache', fullfile(prefdir, 'pprz_local_cache'), 'ok', false, ...
        'problems', {{}});
    if ~isfile(cfg.script)
        cfg.problems{end + 1} = sprintf('%s is missing: use the matlab folder of a full repository checkout.', cfg.script);
    end

    % Python with h5py and numpy
    candidates = {pref(group, 'python', '')};
    if ispc
        candidates = [candidates, {'py -3', 'python', 'python3'}];
    else
        candidates = [candidates, {'python3', 'python'}];
    end
    candidates = candidates(~cellfun(@isempty, candidates));
    python = '';
    for k = 1:numel(candidates)
        if system([quote_cmd(candidates{k}) ' -c "import sys; sys.exit(sys.version_info < (3, 8))"']) == 0
            python = candidates{k};
            break
        end
    end
    if isempty(python)
        cfg.problems{end + 1} = ['Python 3 was not found. Install it (python.org; on Windows tick "Add to PATH") ' ...
            'or give its path: pprz_setup(''python'', ''C:\...\python.exe'')'];
    else
        modules = system([quote_cmd(python) ' -c "import h5py, numpy"']) == 0;
        if ~modules && opt.install
            fprintf('Installing h5py and numpy with pip…\n');
            system([quote_cmd(python) ' -m pip install --user h5py numpy']);
            modules = system([quote_cmd(python) ' -c "import h5py, numpy"']) == 0;
        end
        if modules
            cfg.python = python;
            setpref(group, 'python', python);
        else
            cfg.problems{end + 1} = sprintf(['Python is missing h5py/numpy: run pprz_setup(''install'', true) ' ...
                'or in a terminal: %s -m pip install h5py numpy'], python);
        end
    end

    % Parser: setting, self-built, downloaded
    exe = 'paparazzi_log_parsing';
    if ispc
        exe = [exe '.exe'];
    end
    found = pref(group, 'parser', '');
    if ~isempty(found) && ~isfile(found)
        found = '';
    end
    built = {fullfile(root, 'cpp', 'build', exe), fullfile(root, 'cpp', 'build', 'Release', exe)};
    for k = 1:numel(built)
        if isempty(found) && isfile(built{k})
            found = built{k};
        end
    end
    downloaded = dir(fullfile(prefdir, 'pprz_parser', '*', exe));
    if isempty(found) && ~isempty(downloaded)
        [~, newest] = max([downloaded.datenum]);
        found = fullfile(downloaded(newest).folder, downloaded(newest).name);
    end
    if (isempty(found) || opt.download) && ~opt.quiet
        try
            found = download_parser(exe);
        catch err
            if isempty(found)
                cfg.problems{end + 1} = sprintf(['No parser: %s Build it (see matlab/README.md) or run ' ...
                    'pprz_setup with internet.'], err.message);
            else
                warning('pprz_setup:download', 'Keeping %s: %s', found, err.message);
            end
        end
    end
    if ~isempty(found)
        [status, out] = system([quote_cmd(found) ' --version']);
        if status == 0
            cfg.parser = found;
            cfg.parser_version = strtrim(out);
        else
            cfg.problems{end + 1} = sprintf('The parser %s does not run: %s', found, strtrim(out));
        end
    elseif ~any(contains(cfg.problems, 'parser'))
        cfg.problems{end + 1} = 'No parser found: run pprz_setup with internet, or build cpp/ (see matlab/README.md).';
    end
    cfg.ok = isempty(cfg.problems);
    if ~opt.quiet
        fprintf('Python:  %s\nParser:  %s %s\nCache:   %s\n', blank(cfg.python), blank(cfg.parser), ...
            cfg.parser_version, cfg.cache);
        if cfg.ok
            fprintf('Ready: pprz_load(''LOG.data'') converts .data/.log files, also without internet.\n');
        else
            fprintf(2, '%s\n', cfg.problems{:});
        end
    end
    if nargout == 0
        clear cfg
    end
end

function file = download_parser(exe)
% The parser of the newest parser-v* release on GitHub for this platform, kept in prefdir/pprz_parser/<tag>.
    assets = struct('glnxa64', 'linux-x64', 'maca64', 'macos-arm64', 'win64', 'windows-x64');
    arch = computer('arch');
    if ~isfield(assets, arch)
        error('pprz_setup:platform', 'No released parser for %s;', arch);
    end
    releases = webread('https://api.github.com/repos/tudelft/paparazzi_log_parsing/releases?per_page=50', ...
        weboptions('Timeout', 30));
    if isstruct(releases)
        releases = num2cell(releases);
    end
    tags = cellfun(@(r) r.tag_name, releases, 'UniformOutput', false);
    keep = find(startsWith(tags, 'parser-v'), 1);  % newest first
    if isempty(keep)
        error('pprz_setup:release', 'No parser release found on GitHub;');
    end
    release = releases{keep};
    name = ['paparazzi_log_parsing-' assets.(arch) erase(exe, 'paparazzi_log_parsing')];
    assets_list = release.assets;
    if isstruct(assets_list)
        assets_list = num2cell(assets_list);
    end
    match = find(cellfun(@(a) strcmp(a.name, name), assets_list), 1);
    if isempty(match)
        error('pprz_setup:asset', '%s has no %s;', release.tag_name, name);
    end
    folder = fullfile(prefdir, 'pprz_parser', release.tag_name);
    if ~isfolder(folder)
        mkdir(folder);
    end
    file = fullfile(folder, exe);
    fprintf('Downloading the parser %s (%s)…\n', release.tag_name, assets.(arch));
    websave(file, assets_list{match}.browser_download_url, weboptions('Timeout', 120));
    if ~ispc
        system(['chmod +x ' quote_cmd(file)]);
    end
end

function c = quote_cmd(cmd)
% A program path with spaces in quotes; commands with arguments such as 'py -3' are kept as they are.
    if isfile(cmd) && any(cmd == ' ')
        c = ['"' cmd '"'];
    else
        c = cmd;
    end
end

function v = pref(group, name, default)
    if ispref(group, name)
        v = getpref(group, name);
    else
        v = default;
    end
end

function s = blank(s)
    if isempty(s)
        s = '(not found)';
    end
end
