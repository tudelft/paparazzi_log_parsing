function plot_track(ac_data)
%PLOT_TRACK Flown track: on a map from GPS_INT (lat/lon) when available, else local east/north (ROTORCRAFT_FP).
    if pprz.has(ac_data, 'GPS_INT') && isfield(ac_data.GPS_INT, 'lat')
        g = ac_data.GPS_INT;
        ok = g.lat ~= 0 & g.lon ~= 0;
        if any(ok) && exist('geoplot', 'file')
            geoplot(g.lat(ok), g.lon(ok), 'LineWidth', 1.5);
            hold on;
            geoplot(g.lat(find(ok, 1)), g.lon(find(ok, 1)), 'go', g.lat(find(ok, 1, 'last')), g.lon(find(ok, 1, 'last')), 'rs');
            geobasemap('satellite');
            title('Track (GPS)');
            return
        end
    end
    if ~pprz.has(ac_data, 'ROTORCRAFT_FP')
        return
    end
    fp = ac_data.ROTORCRAFT_FP;
    plot3(fp.east, fp.north, fp.up, 'LineWidth', 1.2);
    hold on;
    plot3(fp.carrot_east, fp.carrot_north, fp.carrot_up, '--');
    grid on; axis equal;
    xlabel('East [m]'); ylabel('North [m]'); zlabel('Up [m]');
    legend('Flown', 'Target');
    title('Track (local)');
end
