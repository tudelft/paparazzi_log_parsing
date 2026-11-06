function mat = convert(data)
%CONVERT The whole-log MATLAB export of a local .data (or .data.gz) with its .log next to it, made offline with
%   the parser and Python of pprz_setup (webui/matlab_export.py, the same file as the logbook's "Whole log").
%   The .mat is written next to the .data and reused while it is newer than the .data and the parser; when that
%   folder is read-only (an SD card), it goes to the cache folder of pprz_setup.
    found = dir(char(data));  % also expands ~ and relative paths: the converter gets an absolute path
    if numel(found) ~= 1 || found.isdir
        error('pprz:convert', '%s not found.', char(data));
    end
    data = fullfile(found.folder, found.name);
    [folder, name, ext] = fileparts(data);
    if strcmpi(ext, '.gz')
        [~, name] = fileparts(name);
    end
    cfg = pprz_setup('quiet', true);
    target = fullfile(folder, [name '_whole_log.mat']);
    if ~writable(folder)
        if ~isfolder(cfg.cache)
            mkdir(cfg.cache);
        end
        target = fullfile(cfg.cache, [name '_whole_log.mat']);
    end
    source = dir(data);
    if isfile(target)
        made = dir(target);
        parser = dir(cfg.parser);
        if made.datenum >= source.datenum && (isempty(parser) || made.datenum >= parser.datenum)
            mat = target;
            return
        end
    end
    if ~cfg.ok
        error('pprz:setup', 'Cannot convert %s:\n%s\nRun pprz_setup.', data, strjoin(cfg.problems, newline));
    end
    fprintf('Converting %s%s …\n', name, ext);
    cmd = sprintf('%s "%s" "%s" --parser "%s" -o "%s"', quote(cfg.python), cfg.script, data, cfg.parser, target);
    [status, out] = system(cmd);
    if status ~= 0 || ~isfile(target)
        error('pprz:convert', 'Converting %s failed:\n%s', data, strtrim(out));
    end
    mat = target;
end

function ok = writable(folder)
    [~, unique_name] = fileparts(tempname);
    probe = fullfile(folder, ['.pprz_write_test_' unique_name]);
    fid = fopen(probe, 'w');
    ok = fid >= 0;
    if ok
        fclose(fid);
        delete(probe);
    end
end

function c = quote(cmd)
    if isfile(cmd) && any(cmd == ' ')
        c = ['"' cmd '"'];
    else
        c = cmd;
    end
end
