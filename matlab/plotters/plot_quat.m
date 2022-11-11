function plot_quat(meas_quat, ref_quat)
    index = 1;
    ref_x_vector = quat_comp(ref_quat, quat_comp([0 1 0 0], quat_inv(ref_quat)));
    meas_x_vector = quat_comp(meas_quat, quat_comp([0 1 0 0], quat_inv(meas_quat)));

    ref_y_vector = quat_comp(ref_quat, quat_comp([0 0 1 0], quat_inv(ref_quat)));
    meas_y_vector = quat_comp(meas_quat, quat_comp([0 0 1 0], quat_inv(meas_quat)));

    ref_z_vector = quat_comp(ref_quat, quat_comp([0 0 0 1], quat_inv(ref_quat)));
    meas_z_vector = quat_comp(meas_quat, quat_comp([0 0 0 1], quat_inv(meas_quat)));

    figure;
    hold on;
    quiver3(0, 0, 0, ref_x_vector(index, 2), ref_x_vector(index, 3), -ref_x_vector(index, 4));
    quiver3(0, 0, 0, ref_y_vector(index, 2), ref_y_vector(index, 3), -ref_y_vector(index, 4));
    quiver3(0, 0, 0, ref_z_vector(index, 2), ref_z_vector(index, 3), -ref_z_vector(index, 4));

    quiver3(0, 0, 0, meas_x_vector(index, 2), meas_x_vector(index, 3), -meas_x_vector(index, 4));
    quiver3(0, 0, 0, meas_y_vector(index, 2), meas_y_vector(index, 3), -meas_y_vector(index, 4));
    quiver3(0, 0, 0, meas_z_vector(index, 2), meas_z_vector(index, 3), -meas_z_vector(index, 4));

    legend('xref', 'yref', 'zref', 'xmeas', 'ymeas', 'zmeas');
    axis equal;
    grid on;
    xlabel('N');
    ylabel('E');
    zlabel('D');
end