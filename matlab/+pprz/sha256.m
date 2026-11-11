function h = sha256(file)
%SHA256 Lower-case hex SHA-256 of a file (streamed; uses Java, no toolbox), as the logbook identifies logs.
    md = java.security.MessageDigest.getInstance('SHA-256');
    fid = fopen(file, 'r');
    if fid < 0
        error('pprz:sha256', 'Cannot read %s', file);
    end
    closer = onCleanup(@() fclose(fid));
    while true
        block = fread(fid, 2^22, '*int8');
        if isempty(block)
            break
        end
        md.update(block);
    end
    h = lower(reshape(dec2hex(typecast(md.digest(), 'uint8'), 2)', 1, []));
end
