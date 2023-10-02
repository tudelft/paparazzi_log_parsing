function plot_airspeed(ac_data)
    
    ax1 = subplot(2,1,1);
    if isfield(ac_data, 'AIR_DATA')
        % Plot the Calibrated airspeed data
        plot(ac_data.AIR_DATA.timestamp, [ac_data.AIR_DATA.airspeed]);
    end
    
    grid on;
    ylabel('Airspeed [m/s]');
    title('Air-Data');
    xlabel('Time [s]');
   
    ax2 = subplot(2,1,2);
    hold off
    legend_array = []
    if isfield(ac_data, 'AIRSPEED_RAW')
        sensors = unique(ac_data.AIRSPEED_RAW.sensor_id);
        for s = 1:size(sensors,1)
            sid = sensors(s);
            i = find(ac_data.AIRSPEED_RAW.sensor_id == sid);
%            plot(ac_data.AIRSPEED_RAW.timestamp(i), ac_data.AIRSPEED_RAW.airspeed(i));
            plot(ac_data.AIRSPEED_RAW.timestamp(i), ac_data.AIRSPEED_RAW.raw(i));
%            plot(ac_data.AIRSPEED_RAW.timestamp(i), ac_data.AIRSPEED_RAW.diffPress(i));
            hold on
            plot(ac_data.AIRSPEED_RAW.timestamp(i), ac_data.AIRSPEED_RAW.offset(i));
            legend_array = [legend_array; "ID=" + num2str(sid) ]
        end
    end
    
    legend(legend_array)
    
    title('RAW Airspeeds');
    xlabel('Time [s]');
%    legend('Power [W]');

    sgtitle('Energy')
    linkaxes([ax1, ax2],'x')
end