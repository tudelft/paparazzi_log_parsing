function plot_energy(ac_data, vert)
    % Plot the Energy message
    if isfield(ac_data, 'ENERGY')
        figure(10);
        ax1 = subplot(3,1,1);
        plot(ac_data.ENERGY.timestamp, [ac_data.ENERGY.voltage]);
        if exist('vert','var')
            hold on;
            plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
        end
        title('Energy Voltage');
        xlabel('Time [s]');
        legend('Voltage [V]');

        ax2 = subplot(3,1,2);
        plot(ac_data.ENERGY.timestamp, [ac_data.ENERGY.current]);
        if exist('vert','var')
            hold on;
            plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
        end
        title('Energy Current');
        xlabel('Time [s]');
        legend('Current [A]');

        ax3 = subplot(3,1,3);
        plot(ac_data.ENERGY.timestamp, [ac_data.ENERGY.power]);
        if exist('vert','var')
            hold on;
            plot([vert'; vert'], repmat(ylim',1,size(vert,1)), '--r');
        end
        title('Energy Power');
        xlabel('Time [s]');
        legend('Power [W]');

        sgtitle('Energy')
        linkaxes([ax1, ax2, ax3],'x')
    end
    end