/*
Copyright (c) Facebook, Inc. and its affiliates.
All rights reserved.

This source code is licensed under the BSD-style license found in the
LICENSE file in the root directory of this source tree.
*/

package utils_test

import (
	"github.com/stretchr/testify/assert"
	"magma/lte/cloud/go/services/cellular/utils"
	"testing"
)

func TestGetBand(t *testing.T) {
	expected := map[int32]int32{
		0:     1,
		599:   1,
		600:   2,
		749:   2,
		38650: 40,
		43590: 43,
		45589: 43,
	}

	for earfcndl, bandExpected := range expected {
		band, err := utils.GetBand(earfcndl)
		assert.NoError(t, err)
		assert.Equal(t, bandExpected, band.ID)
	}
}

func TestGetBandError(t *testing.T) {
	expectedErr := [...]int32{-1, 45590, 45591}

	for _, earfcndl := range expectedErr {
		_, err := utils.GetBand(earfcndl)
		assert.Error(t, err, "Invalid EARFCNDL: no matching band")
	}
}
