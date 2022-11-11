function plot_esc(ac_data, idx)
    if ~isfield(ac_data, 'ESC')
        return
    end
    
    % Plot the ESC message
    if ~exist('idx','var')
        idx = unique(ac_data.ESC.motor_id);
    end

    ax1 = subplot(6,1,1);
    for i=1:length(idx)
        m = find(ac_data.ESC.motor_id == idx(i));
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.rpm(m));
        hold on;
    end
    title('RPM');
    xlabel('Time [s]');
    legend(strcat('RPM',string(idx)));

    ax2 = subplot(6,1,2);
    for i=1:length(idx)
        m = find(ac_data.ESC.motor_id == idx(i));
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.motor_volts(m));
        hold on;
    end
    title('Voltage');
    xlabel('Time [s]');
    legend(strcat('Volt',string(idx),' [v]'));

    ax3 = subplot(6,1,3);
    for i=1:length(idx)
        m = find(ac_data.ESC.motor_id == idx(i));
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.amps(m));
        hold on;
    end
    title('Current');
    xlabel('Time [s]');
    legend(strcat('Current',string(idx),' [A]'));

    ax4 = subplot(6,1,4);
    for i=1:length(idx)
        m = find(ac_data.ESC.motor_id == idx(i));
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.power(m));
        hold on;
    end
    title('Power');
    xlabel('Time [s]');
    legend(strcat('Power',string(idx),' [W]'));

    ax5 = subplot(6,1,5);
%         for i=1:length(idx)
%             m = find(ac_data.ESC.motor_id == idx(i));
%             plot(ac_data.ESC.timestamp(m), ac_data.ESC.errors(m));
%             hold on;
%         end
%         title('Errors');
%         xlabel('Time [s]');
%         legend(strcat('ERR',string(idx)));

    ax6 = subplot(6,1,6);
    for i=1:length(idx)
        m = find(ac_data.ESC.motor_id == idx(i));
        plot(ac_data.ESC.timestamp(m), ac_data.ESC.temperature(m));
        hold on;
    end
    title('Temperature');
    xlabel('Time [s]');
    legend(strcat('Temp ',string(idx), ' [C]'));

    sgtitle('ESC')
    linkaxes([ax1,ax2,ax3,ax4,ax5,ax6],'x')
end