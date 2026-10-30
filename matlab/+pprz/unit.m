function u = unit(f, msg, field)
%UNIT The unit of a stored field (the alternate unit where the export converted it), '' when not known.
%   u = pprz.unit(f, 'IMU_GYRO_SCALED', 'gp')   % 'deg/s'
    u = '';
    try
        m = f.messages.(msg);
        k = find(strcmp(cellstr(m.fields), field), 1);
        if ~isempty(k)
            units = cellstr(m.units);
            u = units{k};
        end
    catch
    end
end
